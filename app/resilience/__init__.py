"""Timeouts, retries and circuit breakers around every call that leaves the process.

The rule the package exists to keep: a dependency failure costs a bounded amount of time and
then becomes an answer. Nothing here makes a failing dependency work. It makes the failure
cheap, visible, and shaped like an HTTP response.
"""
