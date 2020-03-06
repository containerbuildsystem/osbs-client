"""
Copyright (c) 2020 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import

import pytest

from osbs.utils.labels import Labels


@pytest.mark.parametrize(('labels', 'fnc', 'expect'), [
    ({},
     ("get_name", Labels.LABEL_TYPE_COMPONENT),
     "com.redhat.component"),
    ({},
     ("get_name", "doesnt_exist"),
     Exception),
    ({"Name": "old",
      "name": "new"},
     ("get_name", Labels.LABEL_TYPE_NAME),
     "name"),
    ({"Name": "old"},
     ("get_name", Labels.LABEL_TYPE_NAME),
     "Name"),
    ({"Name": "old"},
     ("get_name", None),
     TypeError),  # arg is required
    ({},
     ("get_new_names_by_old", None),
     {"Vendor": "vendor", "Name": "name", "Build_Host": "com.redhat.build-host",
      "Version": "version", "Architecture": "architecture",
      "Release": "release", "BZComponent": "com.redhat.component",
      "Authoritative_Registry": "authoritative-source-url",
      "RUN": "run",
      "INSTALL": "install",
      "UNINSTALL": "uninstall"}),
    ({"Name": "old",
      "name": "new"},
     ("get_name_and_value", Labels.LABEL_TYPE_NAME),
     ("name", "new")),
    ({},
     ("get_name_and_value", Labels.LABEL_TYPE_NAME),
     KeyError),
    ({},
     ("get_name_and_value", "doest_exist"),
     Exception),
    ({"com.redhat.delivery.appregistry": "true"},
     ("get_name_and_value", Labels.LABEL_TYPE_OPERATOR_MANIFESTS),
     ("com.redhat.delivery.appregistry", "true")),
    ({"com.redhat.delivery.operator.bundle": "true"},
     ("get_name_and_value", Labels.LABEL_TYPE_OPERATOR_BUNDLE_MANIFESTS),
     ("com.redhat.delivery.operator.bundle", "true")),
])
def test_labels(labels, fnc, expect):
    label = Labels(labels)

    fn, arg = fnc
    if isinstance(expect, type):
        with pytest.raises(expect):
            if arg is not None:
                assert getattr(label, fn)(arg) == expect
            else:
                assert getattr(label, fn)() == expect
    else:
        if arg is not None:
            assert getattr(label, fn)(arg) == expect
        else:
            assert getattr(label, fn)() == expect
