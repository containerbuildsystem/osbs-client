"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


Exceptions raised by OSBS
"""
from __future__ import print_function, absolute_import, unicode_literals

import json
from requests.utils import guess_json_utf
import six
from traceback import format_tb


class OsbsException(Exception):
    def __init__(self, message=None, cause=None, traceback=None):
        if message is None and cause is not None:
            message = str(cause)

        super(OsbsException, self).__init__(message)
        self.message = message
        self.cause = cause
        self.traceback = traceback

    def __str__(self):
        if self.cause and self.traceback and not hasattr(self, '__context__'):
            return ("%s\n\n" % self.message +
                    "Original traceback (most recent call last):\n" +
                    "".join(format_tb(self.traceback)) +
                    "%r" % self.cause)
        else:
            return super(OsbsException, self).__str__()

    def __repr__(self):
        if self.cause and not hasattr(self, '__context__'):
            return "OsbsException: %s (from %r)" % (self.message, self.cause)
        else:
            return super(OsbsException, self).__repr__()


class OsbsResponseException(OsbsException):
    """ OpenShift didn't respond with OK (200) status """

    def __init__(self, message, status_code, *args, **kwargs):
        super(OsbsResponseException, self).__init__(message, *args, **kwargs)
        self.status_code = status_code

        # try decoding openshift Status object
        # https://docs.openshift.org/latest/rest_api/openshift_v1.html#v1-status
        if isinstance(message, six.binary_type):
            encoding = guess_json_utf(message)
            message = message.decode(encoding)

        try:
            self.json = json.loads(message)
        except ValueError:
            self.json = None


class OsbsNetworkException(OsbsException):
    """ cURL returned an error """
    def __init__(self, url, message, status_code, *args, **kwargs):
        super(OsbsNetworkException, self).__init__("(%s) %s" % (status_code,
                                                                message),
                                                   *args, **kwargs)
        self.url = url
        self.status_code = status_code


class OsbsAuthException(OsbsException):
    pass


class OsbsValidationException(OsbsException):
    pass


class OsbsLocallyModified(OsbsException):
    """Local modifications found in repo"""


class OsbsCommitNotFound(OsbsException):
    """Commit was not found in repo"""
