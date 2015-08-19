"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import pytest

from osbs.build.spec import BuildIDParam
from osbs.exceptions import OsbsValidationException


class TestBuildIDParam(object):
    def test_build_id_param_shorten_id(self):
        p = BuildIDParam()
        p.value = "x" * 63

        val = p.value

        assert len(val) == 63

    def test_build_id_param_raise_exc(self):
        p = BuildIDParam()
        with pytest.raises(OsbsValidationException):
            p.value = r"\\\\@@@@||||"
