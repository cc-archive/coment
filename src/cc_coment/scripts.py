import os
from django.core import management

def manage():

    import coment.settings as mod

    management.execute_manager(mod)


def fcgi():
    
    from cc_coment.FcgiWsgiAdapter import list_environment, serve_wsgi
    from django.core.handlers.wsgi import WSGIHandler

    # use this to test that fastcgi works and to inspect the environment
    # serve_wsgi(list_environment)
    # use this to serve django

    serve_wsgi(WSGIHandler())

def noop():
    """A no-op callable we can list as a "script" in setup.py in order
    to get the PYTHONPATH configured to our liking."""

