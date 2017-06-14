# Testing

When writing tests for new functionality there are some things to be
aware of.

Because osbs-client is used to convey queries to and responses from a
server, unit testing needs to be able to provide the server-side part
of that exchange.

This is achieved in tests/fake_api.py, which provides a pytest fixture
called "openshift". When unit tests call for an argument named
"openshift", the fixture provides this value by creating an instance
of Openshift but using a special class, Connection, to provide the HTTP
functionality.

Other fixtures, such as "osbs" and "osbs106", make use of this fixture
as well.

For all these fixtures, HTTP requests made during the unit test are
intercepted and handled by the Connection class, which maps requests
and methods to files within the tests/mock_jsons directory.

When new functionality is going to make a request not already handled
by this class, you will need to:

* capture the response returned by the server

* store this in tests/mock_jsons/(version)/{whatever}.json

* update Connections.DEFINITION (defined in its constructor) in
  tests/fake_api.py to know where to find this file for the request
  your unit test will try to send

## Capturing responses

One way to capture responses is to send the requests manually using
curl, like this:

```
TOKEN=$(oc whoami -t)
curl -H "Authorization: Bearer $TOKEN" https://...
```

An alternative, for requests that are sent as a result of an osbs-client CLI operation, is to supply the --capture-dir parameter:

```
osbs --capture-dir response-captures/ CMD PARAMS...
```
