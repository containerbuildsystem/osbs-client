"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import json
import os
import logging
from requests.utils import guess_json_utf


logger = logging.getLogger(__name__)


class IterLinesSaver(object):
    """
    Wrap HttpStream.iter_lines() and save responses.
    """

    def __init__(self, path, fn):
        self.path = path
        self.fn = fn
        self.line = 0

    def iter_lines(self):
        encoding = None
        for line in self.fn():
            path = "{f}-{n:0>3}.json".format(f=self.path, n=self.line)
            logger.debug("capturing to %s", path)

            if not encoding:
                encoding = guess_json_utf(line)

            with open(path, "w") as outf:
                try:
                    json.dump(json.loads(line.decode(encoding)), outf,
                              sort_keys=True, indent=4)
                except ValueError:
                    outf.write(line)

            self.line += 1
            yield line


class ResponseSaver(object):
    """
    Wrap HttpSession.request() and save responses.
    """

    def __init__(self, capture_dir, openshift_api_uri, k8s_api_uri, fn):
        self.capture_dir = capture_dir
        self.openshift_api_uri = openshift_api_uri
        self.k8s_api_uri = k8s_api_uri
        self.fn = fn
        self.visited = {}

    def request(self, url, method, *args, **kwargs):
        filename = url
        if filename.startswith(self.openshift_api_uri):
            filename = filename[len(self.openshift_api_uri):]
        if filename.startswith(self.k8s_api_uri):
            filename = filename[len(self.k8s_api_uri):]
        filename = filename.replace('/', '_')
        path = os.path.join(self.capture_dir,
                            "{method}-{url}".format(method=method,
                                                    url=filename))

        visit = self.visited.get(path, 0)
        self.visited[path] = visit + 1
        path += "-{0:0>3}".format(visit)

        if kwargs.get('stream', False):
            stream = self.fn(url, method, *args, **kwargs)
            stream.iter_lines = IterLinesSaver(path,
                                               stream.iter_lines).iter_lines
            return stream
        else:
            response = self.fn(url, method, *args, **kwargs)
            logger.debug("capturing to %s.json", path)

            encoding = guess_json_utf(response.content)
            with open(path + ".json", "w") as outf:
                try:
                    json.dump(json.loads(response.content.decode(encoding)),
                              outf, sort_keys=True, indent=4)
                except ValueError:
                    outf.write(response.content)

            return response


def setup_json_capture(osbs, os_conf, capture_dir):
    """
    Only used for setting up the testing framework.
    """

    try:
        os.mkdir(capture_dir)
    except OSError:
        pass
    finally:
        osbs.os._con.request = ResponseSaver(capture_dir,
                                             os_conf.get_openshift_api_uri(),
                                             os_conf.get_k8s_api_uri(),
                                             osbs.os._con.request).request
