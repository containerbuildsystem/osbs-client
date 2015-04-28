#!/bin/bash
#
# Copyright (c) 2015 Red Hat, Inc
# All rights reserved.
#
# This software may be modified and distributed under the terms
# of the BSD license. See the LICENSE file for details.

set -ex

# /*
#  * CONFIGURATION
#  */
#
# You should set these opts:
#
#    export OS_PROXY_URL="http://auth_proxy:9443"
#    export OS_URL="https://openshift_master:8443"
#    export CURL_AUTH_OPTS="-u test_user:password"
#    export OS_BUILD="name-of-build-within-openshift"

OS_VERSION="${OS_VERSION:-0.4.1}"

if [ -n "$OS_PROXY_URL" ] ; then
  HTTPD_OSAPI_URL="${OS_PROXY_URL}/osapi/v1beta1"
  HTTPD_AUTH_URL="${OS_PROXY_URL}/oauth/authorize?client_id=openshift-challenging-client&response_type=token"
fi
if [ -n "$OS_URL" ] ; then
  OS_OSAPI_URL="${OS_URL}/osapi/v1beta1"
  OS_AUTH_URL="${OS_URL}/oauth/authorize?client_id=openshift-challenging-client&response_type=token"
fi

CURL_OPTS="--insecure -vsS"
ACCESS_TOKEN=""
CURL="curl ${CURL_OPTS}"
CURL_AUTH="${CURL} ${CURL_AUTH_OPTS}"

# /*
#  * RUNTIME
#  */

DIR=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )
STORE_PATH="${DIR}/mock_jsons/${OS_VERSION}"
mkdir -p $STORE_PATH

save_output_to() {
  local content=$(cat)
  local ext=${2:-json}
  local file_name="${1}.${ext}"
  if [ "$ext" == "json" ] ; then
    echo "$content" | python -m json.tool >${STORE_PATH}/${file_name}
  else
    echo "$content" >${STORE_PATH}/${file_name}
  fi
}

set_access_token() {
  if [ -n "$HTTPD_AUTH_URL" ] ; then
    local curl_output="$(${CURL_AUTH} ${HTTPD_AUTH_URL} 2>&1)"
    printf "${curl_output}" | save_output_to authorize txt
    ACCESS_TOKEN=`${CURL_AUTH} ${HTTPD_AUTH_URL} 2>&1 | egrep -o 'access_token=[^&]+'`
  fi
}

set_access_token

if [ -n "$OS_OSAPI_URL" ] ; then
  $CURL "${OS_OSAPI_URL}/users/~?${ACCESS_TOKEN}" | save_output_to "get_user"
  $CURL $OS_OSAPI_URL/builds | save_output_to "builds_list"
  $CURL $OS_OSAPI_URL/builds/${OS_BUILD} | save_output_to "build_${OS_BUILD}"
  $CURL -X PUT -H "Expect:" -H "Content-Type: application/json" -d @${STORE_PATH}/build_${OS_BUILD}.json $OS_OSAPI_URL/builds/${OS_BUILD} | save_output_to "build_put"
fi
