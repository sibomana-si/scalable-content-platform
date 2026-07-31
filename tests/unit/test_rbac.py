"""Unit tests for the RBAC route policy: pure functions, no I/O.

``route_requirement`` classifies a (method, path) into a required access level, and
``authorize`` decides whether a principal satisfies it. The 401-before-403 ordering
(authentication is checked before role) is pinned here at the policy level.
"""

from app.api.middleware import Requirement, authorize, route_requirement


class TestRouteRequirement:
    def test_article_reads_are_public(self):
        assert route_requirement("GET", "/v1/articles") is Requirement.PUBLIC
        assert route_requirement("GET", "/v1/articles/123") is Requirement.PUBLIC

    def test_article_writes_require_authentication(self):
        for method in ("POST", "PUT", "DELETE"):
            assert route_requirement(method, "/v1/articles/1") is Requirement.AUTHENTICATED

    def test_auth_health_and_docs_are_public(self):
        assert route_requirement("POST", "/v1/auth/login") is Requirement.PUBLIC
        assert route_requirement("POST", "/v1/auth/register") is Requirement.PUBLIC
        assert route_requirement("GET", "/health/live") is Requirement.PUBLIC
        assert route_requirement("GET", "/health/ready") is Requirement.PUBLIC
        assert route_requirement("GET", "/docs") is Requirement.PUBLIC
        assert route_requirement("GET", "/openapi.json") is Requirement.PUBLIC

    def test_admin_prefix_requires_admin(self):
        assert route_requirement("POST", "/v1/admin/roles") is Requirement.ADMIN

    def test_unknown_route_defaults_to_authenticated(self):
        # Secure default: an unclassified route is never accidentally public.
        assert route_requirement("POST", "/v1/something-new") is Requirement.AUTHENTICATED


class TestAuthorize:
    USER = {"id": "1", "role": "user"}
    ADMIN = {"id": "2", "role": "admin"}

    def test_public_allows_anonymous(self):
        assert authorize(Requirement.PUBLIC, None) is None

    def test_authenticated_requires_a_principal(self):
        denial = authorize(Requirement.AUTHENTICATED, None)
        assert denial is not None and denial[0] == 401

    def test_authenticated_allows_any_principal(self):
        assert authorize(Requirement.AUTHENTICATED, self.USER) is None

    def test_admin_forbids_non_admin(self):
        denial = authorize(Requirement.ADMIN, self.USER)
        assert denial is not None and denial[0] == 403

    def test_admin_allows_admin(self):
        assert authorize(Requirement.ADMIN, self.ADMIN) is None

    def test_missing_principal_is_401_before_admin_403(self):
        # Authentication failure (401) must take precedence over the role check (403).
        denial = authorize(Requirement.ADMIN, None)
        assert denial is not None and denial[0] == 401
