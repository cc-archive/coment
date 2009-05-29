#!/usr/bin/python
'''
FcgiWsgiAdapter.py

serves wsgi requests over fcgi. Basic usage in wsgi script:

from FcgiWsgiAdapter import serve
serve(myWsgiApp)

The fcgi part of this was adopted wholesale, with minor modifications, 
from Robin Dunn's fastcgi module; the original preamble of that module 
is further down.
---
To use with django and mod_fcgid, you need a cgi script, say django.cgi:

#!/usr/bin/python
from FcgiWsgiAdapter import list_environment, serve_wsgi

import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'  # or whatever
from django.core.handlers.wsgi import WSGIHandler

# use this to test that fastcgi works and to inspect the environment
# serve_wsgi(list_environment)
# use this to serve django
serve_wsgi(WSGIHandler())
-----
Apache config example:

RewriteRule ^/(~[^/]+)/django/(.*)$ /$1/django/django.cgi/$2 [PT]
<Directory /home/*/public_html/django/>
    Options ExecCGI
    AddHandler fcgid-script .cgi
    # AddHandler cgi-script .cgi
</Directory>

This would invoke django for any "/~joe/django/joes_app" request.
This configuration example also works with suexec, which would be 
useful in a multi-user context. 

Commenting out fcgid-script and uncommenting cgi-script (and a 
server restart) will switch the execution mode from fastcgi 
to plain cgi, without any further changes being required 
anywhere else.

For simpler urls when serving a single site, you could do

RewriteRule ^/mysite/(.*)$ /~fakeuser/django/django.cgi/$1 [PT]
<Directory /home/fakeuser/public_html/django/>
    Options ExecCGI
    AddHandler fcgid-script .cgi
</Directory>

This config would still give you suexec in a typical apache 
install, since [PT] applies the output of mod_rewrite to 
suexec and ~fakeuser is a user directory, which suexec likes.
'''

#
#               Copyright (c) 1998 by Total Control Software
#                         All Rights Reserved
#
#
# Module Name:  fcgi.py
#
# Description:  Handles communication with the FastCGI module of the
#               web server without using the FastCGI developers kit, but
#               will also work in a non-FastCGI environment, (straight CGI.)
#               This module was originally fetched from someplace on the
#               Net (I don't remember where and I can't find it now...) and
#               has been significantly modified to fix several bugs, be more
#               readable, more robust at handling large CGI data and return
#               document sizes, and also to fit the model that we had previously
#               used for FastCGI.
#
#     WARNING:  If you don't know what you are doing, don't tinker with this
#               module!
#
# Creation Date:    1/30/98 2:59:04PM
#
# License:      This is free software.  You may use this software for any
#               purpose including modification/redistribution, so long as
#               this header remains intact and that you do not claim any
#               rights of ownership or authorship of this software.  This
#               software has been tested, but no warranty is expressed or
#               implied.
#
#

# minor modifications by Michael Palmer:
# - reduce string copying operations when writing output
# - some updates to more recent python syntax


import  os, sys, string, socket, errno, traceback
from    cStringIO   import StringIO

from wsgiref.handlers import BaseCGIHandler

#

# Set various FastCGI constants
# Maximum number of requests that can be handled
FCGI_MAX_REQS=1
FCGI_MAX_CONNS = 1

# Supported version of the FastCGI protocol
FCGI_VERSION_1 = 1

# Boolean: can this application multiplex connections?
FCGI_MPXS_CONNS=0

# Record types
FCGI_BEGIN_REQUEST = 1 ; FCGI_ABORT_REQUEST = 2 ; FCGI_END_REQUEST   = 3
FCGI_PARAMS        = 4 ; FCGI_STDIN         = 5 ; FCGI_STDOUT        = 6
FCGI_STDERR        = 7 ; FCGI_DATA          = 8 ; FCGI_GET_VALUES    = 9
FCGI_GET_VALUES_RESULT = 10
FCGI_UNKNOWN_TYPE = 11
FCGI_MAXTYPE = FCGI_UNKNOWN_TYPE

# Types of management records
ManagementTypes = [FCGI_GET_VALUES]

FCGI_NULL_REQUEST_ID=0

# Masks for flags component of FCGI_BEGIN_REQUEST
FCGI_KEEP_CONN = 1

# Values for role component of FCGI_BEGIN_REQUEST
FCGI_RESPONDER = 1 ; FCGI_AUTHORIZER = 2 ; FCGI_FILTER = 3

# Values for protocolStatus component of FCGI_END_REQUEST
FCGI_REQUEST_COMPLETE = 0               # Request completed nicely
FCGI_CANT_MPX_CONN    = 1               # This app can't multiplex
FCGI_OVERLOADED       = 2               # New request rejected; too busy
FCGI_UNKNOWN_ROLE     = 3               # Role value not known

# chunk size for writing data
CHUNK_SIZE = 2**13  # 8 kB - tried 64 kB but it fucks up. 8 kB is fast enough.

error = 'fcgi.error'

# The following function is used during debugging; it isn't called
# anywhere at the moment

def error(msg):
    "Append a string to /tmp/err"
    errf=open('/tmp/err', 'a+')
    errf.write(msg +'\n')
    errf.close()

class record:
    "Class representing FastCGI records"
    def __init__(self):
        self.version = FCGI_VERSION_1
        self.recType = FCGI_UNKNOWN_TYPE
        self.reqId   = FCGI_NULL_REQUEST_ID
        self.content = ""

    #
    def readRecord(self, sock):
        s = map(ord, sock.recv(8))
        self.version, self.recType, paddingLength = s[0], s[1], s[6]
        self.reqId, contentLength = (s[2]<<8)+s[3], (s[4]<<8)+s[5]
        self.content = ""
        while len(self.content) < contentLength:
            data = sock.recv(contentLength - len(self.content))
            self.content = self.content + data
        if paddingLength != 0:
            padding = sock.recv(paddingLength)

        # Parse the content information
        c = self.content
        if self.recType == FCGI_BEGIN_REQUEST:
            self.role = (ord(c[0])<<8) + ord(c[1])
            self.flags = ord(c[2])

        elif self.recType == FCGI_UNKNOWN_TYPE:
            self.unknownType = ord(c[0])

        elif self.recType == FCGI_GET_VALUES or self.recType == FCGI_PARAMS:
            self.values={}
            pos=0
            while pos < len(c):
                name, value, pos = readPair(c, pos)
                self.values[name] = value
        elif self.recType == FCGI_END_REQUEST:
            b = map(ord, c[0:4])
            self.appStatus = (b[0]<<24) + (b[1]<<16) + (b[2]<<8) + b[3]
            self.protocolStatus = ord(c[4])

    #
    def writeRecord(self, sock):
        content = self.content
        if self.recType == FCGI_BEGIN_REQUEST:
            content = chr(self.role>>8) + chr(self.role & 255) + chr(self.flags) + 5*'\000'

        elif self.recType == FCGI_UNKNOWN_TYPE:
            content = chr(self.unknownType) + 7*'\000'

        elif self.recType==FCGI_GET_VALUES or self.recType==FCGI_PARAMS:
            content = ""
            for i in self.values.keys():
                content = content + writePair(i, self.values[i])

        elif self.recType==FCGI_END_REQUEST:
            v = self.appStatus
            content = chr((v>>24)&255) + chr((v>>16)&255) + chr((v>>8)&255) + chr(v&255)
            content = content + chr(self.protocolStatus) + 3*'\000'

        cLen = len(content)
        eLen = (cLen + 7) & (0xFFFF - 7)    # align to an 8-byte boundary
        padLen = eLen - cLen

        hdr = [ self.version,
                self.recType,
                self.reqId >> 8,
                self.reqId & 255,
                cLen >> 8,
                cLen & 255,
                padLen,
                0]
        # hdr = string.joinfields(map(chr, hdr), '')
        hdr = ''.join([chr(h) for h in hdr])

        sock.send(hdr + content + padLen*'\000')

#

def readPair(s, pos):
    nameLen = ord(s[pos])
    pos += 1

    if nameLen & 128:
        b = [ord(x) for x in s[pos:pos+3]]
        pos += 3
        nameLen = ((nameLen&127)<<24) + (b[0]<<16) + (b[1]<<8) + b[2]

    valueLen = ord(s[pos])
    pos += 1

    if valueLen & 128:
        b = [ord(x) for x in s[pos:pos+3]]
        pos += 3
        valueLen=((valueLen&127)<<24) + (b[0]<<16) + (b[1]<<8) + b[2]

    return ( s[pos:pos+nameLen], s[pos+nameLen:pos+nameLen+valueLen],
             pos+nameLen+valueLen )



def writePair(name, value):
    l=len(name)
    if l<128: s=chr(l)
    else:
        s=chr(128|(l>>24)&255) + chr((l>>16)&255) + chr((l>>8)&255) + chr(l&255)
    l=len(value)
    if l<128: s=s+chr(l)
    else:
        s=s+chr(128|(l>>24)&255) + chr((l>>16)&255) + chr((l>>8)&255) + chr(l&255)
    return s + name + value

#

def HandleManTypes(r, conn):
    if r.recType == FCGI_GET_VALUES:
        r.recType = FCGI_GET_VALUES_RESULT
        v={}
        vars={'FCGI_MAX_CONNS' : FCGI_MAX_CONNS,
              'FCGI_MAX_REQS'  : FCGI_MAX_REQS,
              'FCGI_MPXS_CONNS': FCGI_MPXS_CONNS}
        for i in r.values.keys():
            if vars.has_key(i): v[i]=vars[i]
        r.values=vars
        r.writeRecord(conn)

#
#


_isFCGI = 1         # assume it is until we find out for sure

def isFCGI():
    global _isFCGI
    return _isFCGI

#


fcgi_init = None
_sock = None

class FCGI:
    def __init__(self):
        self.haveFinished = 0
        if fcgi_init == None:
            _startup()
        if not isFCGI():
            self.haveFinished = 1
            self.inp, self.out, self.err, self.env = \
                                sys.stdin, sys.stdout, sys.stderr, os.environ
            return

        if os.environ.has_key('FCGI_WEB_SERVER_ADDRS'):
            good_addrs=string.split(os.environ['FCGI_WEB_SERVER_ADDRS'], ',')
            good_addrs=map(string.strip(good_addrs))        # Remove whitespace
        else:
            good_addrs=None

        self.conn, addr=_sock.accept()
        stdin, data="", ""
        self.env = {}
        self.requestId=0
        remaining=1

        # Check if the connection is from a legal address
        if good_addrs!=None and addr not in good_addrs:
            raise error, 'Connection from invalid server!'

        while remaining:
            r = record()
            r.readRecord(self.conn)

            if r.recType in ManagementTypes:
                HandleManTypes(r, self.conn)

            elif r.reqId==0:
                # Oh, poopy.  It's a management record of an unknown
                # type.  Signal the error.
                r2 = record()
                r2.recType = FCGI_UNKNOWN_TYPE ; r2.unknownType=r.recType
                r2.writeRecord(self.conn)
                continue                # Charge onwards

            # Ignore requests that aren't active
            elif r.reqId != self.requestId and r.recType != FCGI_BEGIN_REQUEST:
                continue

            # If we're already doing a request, ignore further BEGIN_REQUESTs
            elif r.recType == FCGI_BEGIN_REQUEST and self.requestId != 0:
                continue

            # Begin a new request
            if r.recType == FCGI_BEGIN_REQUEST:
                self.requestId = r.reqId
                if r.role == FCGI_AUTHORIZER:   remaining=1
                elif r.role == FCGI_RESPONDER:  remaining=2
                elif r.role == FCGI_FILTER:     remaining=3

            elif r.recType == FCGI_PARAMS:
                if r.content == "":
                    remaining=remaining-1
                else:
                    for i in r.values.keys():
                        self.env[i] = r.values[i]

            elif r.recType == FCGI_STDIN:
                if r.content == "":
                    remaining=remaining-1
                else:
                    stdin=stdin+r.content

            elif r.recType==FCGI_DATA:
                if r.content == "":
                    remaining=remaining-1
                else:
                    data=data+r.content
        # end of while remaining:

        self.inp = sys.stdin  = StringIO(stdin)
        self.err = sys.stderr = StringIO()
        self.out = sys.stdout = StringIO()
        #self.data = StringIO(data)

    #def __del__(self): I really don't get what this is good for...
        #self.finish()

    def finish(self, status=0):
        if not self.haveFinished:
            self.haveFinished = 1

            self.err.seek(0,0)
            self.out.seek(0,0)

            r=record()
            r.recType = FCGI_STDERR
            r.reqId = self.requestId
            data = self.err.read()

            chunker = self.datachunker(data)
            for chunk in chunker:
                r.content = chunk
                r.writeRecord(self.conn)
            r.content="" ; r.writeRecord(self.conn)      # Terminate stream

            r.recType = FCGI_STDOUT
            data = self.out.read()

            chunker = self.datachunker(data)

            for chunk in chunker:
                r.content = chunk
                r.writeRecord(self.conn)
            r.content="" ; r.writeRecord(self.conn)      # Terminate stream

            r=record()
            r.recType=FCGI_END_REQUEST
            r.reqId=self.requestId
            r.appStatus=status
            r.protocolStatus=FCGI_REQUEST_COMPLETE
            r.writeRecord(self.conn)
            self.conn.close()
        elif not isFCGI():   # for some reason cgi repeats again and again if we don't do this.
            sys.exit()

    def datachunker(self, data):
        '''
        yield string in chunks for writing
        '''
        c = 0
        cs = CHUNK_SIZE
        d = data[c:c + cs]
        if d:
            c += cs
            yield d
        else:
            raise StopIteration


def _startup():
    global fcgi_init
    fcgi_init = 1
    try:
        s=socket.fromfd(sys.stdin.fileno(), socket.AF_INET,
                        socket.SOCK_STREAM)
        s.getpeername()
    except socket.error, (err, errmsg):
        if err != errno.ENOTCONN:       # must be a non-fastCGI environment
            global _isFCGI
            _isFCGI = 0
            return

    global _sock
    _sock = s


#--------------------------------------------------------------------
# the code below is not part of the original fcgi.py module; it builds
# on the fcgi.py code to run wsgi applications.

# show full traceback if request originated from any of these ips
SHOW_TRACEBACK_IPS = ['127.0.0.1']

class FCGIHandler(BaseCGIHandler):
    '''
    handle a single request received through fcgi
    '''
    wsgi_multithread = False    # the underlying fcgi module is not threaded
    wsgi_multiprocess = True    # might be wrong, depending on your config
    wsgi_run_once = False       # ... likewise
    origin_server = False       # We are not transmitting direct to client, so won't
                                # send http status line and protocol information

    def __init__(self, request):
        BaseCGIHandler.__init__(self, request.inp, request.out, request.err, request.env, \
                                      multithread=self.wsgi_multithread, \
                                      multiprocess=self.wsgi_multiprocess)

    # error handling. this is sent to the client by wsgiref.basehandler.

    def error_output(self, environ, start_response):
        '''
        show traceback if we are so entitled.
        '''
        start_response(self.error_status, self.error_headers[:], sys.exc_info())
        if self.environ.get('REMOTE_ADDR') in SHOW_TRACEBACK_IPS:
            err = traceback.format_exc()
        else:
            err = self.error_body
        return ['wsgi app execution error\n', '-' * 24, '\n', err]


    def run(self, application):
        '''
        Invoke the application
        '''
        self.setup_environ()
        self.result = application(self.environ, self.start_response)
        self.finish_response()


def serve_wsgi(app, handlerClass=FCGIHandler):
    '''
    run (wsgi) app on each incoming FCGI request.
    '''
    while True:
        req = FCGI()
        h = handlerClass(req)
        h.run(app)
        req.finish()
        del req

#------------------------------------------------
# test the thing...
def list_environment(environ, start_response):
    '''
    Simple WSGI test application - just display the wsgi environment.
    Kinda useful, too for debugging.
    '''
    status = '200 OK'
    response_headers = [('Content-type','text/plain')]
    start_response(status, response_headers)
    out = ['FcgiWsgiAdapter test output (just a listing of the WSGI environment)\n']
    out.append('-' * (len(out[0]) -1)  + '\n')

    for k,v in sorted(environ.items()):
        out.extend([k.ljust(25), ': ', str(v).strip(), '\n'])

    return out


def test():
    '''
    to run this, import and run in an actual cgi or fcgi script
    '''
    serve_wsgi(list_environment)