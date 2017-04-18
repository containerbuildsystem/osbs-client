#!/usr/bin/env python
import os
import sys
import json
import re
from osbs.http import HttpSession

os_version = os.getenv("OS_VERSION", "1.1.2")

httpd_osapi_url = None
httpd_auth_url = None
os_osapi_url = None
os_auth_url = None

os_build = os.getenv("OS_BUILD", "")
os_username = os.getenv("OS_USERNAME", "")
os_password = os.getenv("OS_PASSWORD", "")

os_proxy_url = os.getenv("OS_PROXY_URL", None)
if os_proxy_url:
    if int(os_version.split(".")[0]) > 0:
        # Openshift 1.x uses oapi and v1 endpoint
        httpd_osapi_url = "{}/oapi/v1".format(os_proxy_url)
    else:
        httpd_osapi_url = "{}/osapi/v3beta".format(os_proxy_url)
    httpd_auth_url = "{}/oauth/authorize?client_id={}&response_type=token".format(
        os_proxy_url, "openshift-challenging-client")

os_url = os.getenv("OS_URL", None)
if os_url:
    if int(os_version.split(".")[0]) > 0:
        os_osapi_url = "{}/oapi/v1".format(os_url)
    else:
        os_osapi_url = "{}/osapi/v3beta".format(os_url)
    os_auth_url = "{}/oauth/authorize?client_id={}&response_type=token".format(
        os_url, "openshift-challenging-client")

current_dir = os.path.dirname(os.path.realpath(__file__))
store_path = os.path.join(current_dir, "mock_jsons", os_version)
if not os.path.exists(store_path):
    os.makedirs(store_path)


def request_and_save_output_to(url, prefix, request_type='get',
                               headers={}, data={}, auth_only=False, use_json=False):
    request_options = {"verify_ssl": False, "use_json": use_json}
    if auth_only:
        request_options.update({
            "username": os_username, "password": os_password, "allow_redirects": False
        })
    connection = HttpSession(verbose=True)
    print(url)
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
        try:
            content = json.dumps(r.json(), indent=4, separators=(',', ': '))
        except Exception:
            pass

    with open(file_path, 'w') as outfile:
        outfile.write(content)

    if auth_only:
        return str(r.headers)

    return content


def get_access_token(prefix):
    url = None
    if httpd_auth_url:
        url = httpd_auth_url
    else:
        url = os_auth_url
    output = request_and_save_output_to(url, prefix, auth_only=True)
    regexp = r"access_token=[^&]+"
    return re.search(regexp, output).group(0)

access_token = get_access_token("authorize")
print("Got token: {}".format(access_token))

if os_osapi_url:
    url = "{}/users/~?{}".format(os_osapi_url, access_token)
    request_and_save_output_to(url, "get_user")

    url = "{}/namespaces/default/builds".format(os_osapi_url)
    request_and_save_output_to(url, "builds_list")

    url = "{}/namespaces/default/builds/{}".format(os_osapi_url, os_build)
    build_json = request_and_save_output_to(url, "build_{}".format(os_build))

    build_data = json.loads(build_json)
    request_and_save_output_to(url, "build_put", request_type="put", use_json=True, data=build_data)
