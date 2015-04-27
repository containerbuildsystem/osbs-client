"""
Exceptions raised by OSBS
"""

class OsbsException(Exception):
    pass

class OsbsResponseException(OsbsException):
    def __init__ (self, message, status_code, *args, **kwargs):
        super (OsbsException, self).__init__ (message, *args, **kwargs)
        self.status_code = status_code

class OsbsNetworkException(OsbsException):
    def __init__ (self, url, message, status_code, *args, **kwargs):
        super (OsbsNetworkException, self).__init__ (message, *args, **kwargs)
        self.url = url
        self.status_code = status_code
