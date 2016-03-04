#!/usr/bin/env python
import os
import sys
import json
import re
from osbs.http import HttpSession

os_version = os.getenv("OS_VERSION", "1.1.2")

httpd_osapi_url = None
httpd_auth_url = None

os_proxy_url = os.getenv("OS_PROXY_URL", None)
if os_proxy_url:
    httpd_osapi_url = "{}/osapi/v3beta".format(os_proxy_url)
    httpd_auth_url = "{}/oauth/authorize?client_id={}&response_type=token".format(
        os_proxy_url, "openshift-challenging-client")

os_url = os.getenv("OS_URL", None)
if os_url:
    os_osapi_url = "{}/osapi/v3beta".format(os_url)
    os_auth_url = "{}/oauth/authorize?client_id={}&response_type=token".format(
        os_url, "openshift-challenging-client")

current_dir = os.path.dirname(os.path.realpath(__file__))
store_path = os.path.join(current_dir, "mock_jsons", os_version)
if not os.path.exists(store_path):
    os.makedirs(store_path)


def request_and_save_output_to(url, prefix, request_type='get', headers={}, data={}):
    request_options = {"verify_ssl": False, }
    connection = HttpSession(verbose=True)
    r = {
        'get': connection.get,
        'put': connection.put
    }[request_type](url, headers=headers, data=data, **request_options)

    ext = 'json'
    if len(sys.argv) > 1:
        ext = sys.argv[1]

    file_name = "{}.{}".format(prefix, ext)
    file_path = os.path.join(store_path, file_name)
    content = r.content
    if ext == 'json':
        content = json.dumps(r.json(), indent=4, separators=(',', ': '))

    with open(file_path, 'w') as outfile:
        outfile.write(content)

    return content


def get_access_token(prefix):
    if not httpd_auth_url:
        return "5pk5dWSU2KIsGxMsh6RZGkY6aFKGXCWp1ohkBPCuVt8"

    output = request_and_save_output_to(httpd_auth_url, prefix, headers={'X-CSRF-Token': 'aaaa'})
    regexp = r"access_token=([^&]+)"
    return re.search(regexp, output).group(0)

access_token = get_access_token("authorize")

if os_osapi_url:
    url = "{}/users/~?{}".format(os_osapi_url, access_token)
    request_and_save_output_to(url, "get_user")

    url = "{}/namespaces/default/builds".format(os_osapi_url)
    request_and_save_output_to(url, "builds_list")

    os_build = os.getenv("OS_BUILD", "")
    url = "{}/namespaces/default/build/{}".format(os_osapi_url, os_build)
    build_json = request_and_save_output_to(url, "build_{}".format(os_build))

    request_and_save_output_to(
        url, "build_put", request_type="put",
        headers={"Expect": "", "Content-Type": "application/json"},
        data=json.loads(build_json))
