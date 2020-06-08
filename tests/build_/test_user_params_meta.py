"""
Copyright (c) 2020 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import, unicode_literals

import pytest
from flexmock import flexmock

from osbs.build.user_params_meta import BuildParam, BuildParamsBase
from osbs.exceptions import OsbsValidationException


class TestBuildParam(object):
    """
    Basic tests for the BuildParam data descriptor
    """

    def check_basic_properties(self, param, name, mangled_name, repr_s,
                               default=None, required=False, include_in_json=True):
        assert param.name == name
        assert param.required == required
        assert param.include_in_json == include_in_json
        assert param._default == default
        assert param._mangled_name == mangled_name
        assert repr(param) == repr_s

    @pytest.mark.parametrize("default", [None, "some"])
    @pytest.mark.parametrize("required", [True, False])
    @pytest.mark.parametrize("include_in_json", [True, False])
    def test_build_param(self, default, required, include_in_json):
        bp = BuildParam("bp", default=default, required=required, include_in_json=include_in_json)
        self.check_basic_properties(bp,
                                    name="bp",
                                    mangled_name="_BuildParam__bp",
                                    repr_s="BuildParam({!r})".format("bp"),
                                    default=default,
                                    required=required,
                                    include_in_json=include_in_json)

    def test_build_param_subclassing(self):
        class CustomParam(BuildParam):
            pass

        cp = CustomParam("custom")
        self.check_basic_properties(cp,
                                    name="custom",
                                    mangled_name="_CustomParam__custom",
                                    repr_s="CustomParam({!r})".format("custom"))

    @pytest.mark.parametrize("default", [None, "", "some"])
    @pytest.mark.parametrize("value", [None, "", "some"])
    def test_build_param_get_set(self, default, value):
        bp = BuildParam("bp", default=default)
        obj = flexmock()

        assert bp.__get__(obj) == default
        assert not hasattr(obj, "_BuildParam__bp")

        bp.__set__(obj, value)
        assert bp.__get__(obj) == value
        assert obj._BuildParam__bp == value


class TestBuildParamsBase(object):
    """
    Tests for the BuildParamsBase (and, by extension, BuildParamsMeta) class
    """

    class BuildParams(BuildParamsBase):
        """
        Basic BuildParams for testing
        """
        x = BuildParam("x")

    def check_x_value(self, bp_obj, value):
        assert bp_obj.x == value
        assert getattr(bp_obj, "x") == value
        assert getattr(bp_obj, "_BuildParam__x") == value

    def check_basic_properties(self, bp_cls, params_dict, params, required_params):
        assert bp_cls.params_dict == params_dict
        assert bp_cls.params == params
        assert bp_cls.required_params == required_params
        for name, param in params_dict.items():
            assert bp_cls.get_param(name) == param

    def test_param_name_validation(self):
        """
        All BuildParam names must match the name of their attribute
        """
        with pytest.raises(TypeError) as exc_info:
            class BadBuildParams(BuildParamsBase):  # pylint: disable=unused-variable
                x = BuildParam("y")

        assert str(exc_info.value) == "Mismatched param name: x = BuildParam({!r})".format("y")

    def test_injected_class_properties(self):
        """
        Metaclass injects params_dict, params, required_params properties
        and get_param() method
        """
        bpx = BuildParam("x", required=True)
        bpy = BuildParam("y")

        class BuildParams(BuildParamsBase):
            x = bpx
            y = bpy

        self.check_basic_properties(BuildParams,
                                    params_dict={"x": bpx, "y": bpy},
                                    params=[bpx, bpy],
                                    required_params=[bpx])

    def test_injected_class_properties_with_inheritance(self):
        """
        Child classes inherit the params of their parents and can also override them
        """
        bpw = BuildParam("w")
        bpx = BuildParam("x")
        bpy = BuildParam("y")
        bpz = BuildParam("z")

        req_bpx = BuildParam("x", required=True)

        class ParentA(BuildParamsBase):
            w = bpw
            x = bpx

        class ParentB(BuildParamsBase):
            x = req_bpx
            y = bpy

        class ChildA(ParentA):
            # inherits 'w', overrides 'x' with a required 'x', adds 'y'
            x = req_bpx
            y = bpy

        self.check_basic_properties(ChildA,
                                    params_dict={"w": bpw, "x": req_bpx, "y": bpy},
                                    params=[bpw, req_bpx, bpy],
                                    required_params=[req_bpx])

        class ChildB(ParentB):
            # overrides required 'x' with non-required 'x', inherits 'y', adds 'z'
            x = bpx
            z = bpz

        self.check_basic_properties(ChildB,
                                    params_dict={"x": bpx, "y": bpy, "z": bpz},
                                    params=[bpx, bpy, bpz],
                                    required_params=[])

        class ChildAB(ParentA, ParentB):
            # inherits 'w' and non-required 'x' from ParentA, 'y' from ParentB, adds 'z'
            z = bpz

        self.check_basic_properties(ChildAB,
                                    params_dict={"w": bpw, "x": bpx, "y": bpy, "z": bpz},
                                    params=[bpw, bpx, bpy, bpz],
                                    required_params=[])

        class ChildBA(ParentB, ParentA):
            # inherits required 'x' and 'y' from ParentB, 'w' from ParentA
            pass

        self.check_basic_properties(ChildBA,
                                    params_dict={"w": bpw, "x": req_bpx, "y": bpy},
                                    params=[bpw, req_bpx, bpy],
                                    required_params=[req_bpx])

    def test_build_param_access(self):
        """
        Test basic setting and getting of BuildParam values
        """
        bps = self.BuildParams()
        assert bps.x is None
        assert getattr(bps, "x") is None
        assert not hasattr(bps, "_BuildParam__x")

        bps.x = 1
        self.check_x_value(bps, 1)

        setattr(bps, "x", 2)
        self.check_x_value(bps, 2)

    def test_init(self):
        """
        __init__ should set build params from kwargs
        """
        bps = self.BuildParams(x=1)
        self.check_x_value(bps, 1)

    def test_init_unkown_params(self):
        """
        __init__ should fail if any of the kwargs are unknown
        """
        with pytest.raises(OsbsValidationException) as exc_info:
            self.BuildParams(x=1, y=2, w=3)
        assert str(exc_info.value) == "Got unexpected params: 'w', 'y'"

    def test_build_params_instances_do_not_share_values(self):
        """
        Different instances of the same BuildParams class do not share BuildParam values
        """
        bps1 = self.BuildParams()
        bps2 = self.BuildParams()

        bps1.x = 1
        assert bps1.x == 1
        assert bps2.x is None

        bps2.x = 2
        assert bps2.x == 2
        assert bps1.x == 1

    def test_build_param_invalid_access(self):
        """
        Test getting and setting of non-existent attributes
        """
        bps = self.BuildParams()
        with pytest.raises(AttributeError) as exc_info:
            bps.y  # pylint: disable=pointless-statement
        # no magic here, check that you get the default python message
        assert str(exc_info.value) == "'BuildParams' object has no attribute 'y'"

        with pytest.raises(AttributeError) as exc_info:
            bps.y = 1  # pylint: disable=attribute-defined-outside-init
        assert str(exc_info.value) == "No such param: 'y'"

    def test_repr(self):
        """
        The result of repr() should be a valid python expression
        """
        class BuildParams2(BuildParamsBase):
            x = BuildParam("x")
            y = BuildParam("y")

        bps = BuildParams2()
        assert repr(bps) == "BuildParams2(x=None, y=None)"

        bps2 = BuildParams2(x="", y="hello")
        assert repr(bps2) == "BuildParams2(x={!r}, y={!r})".format("", "hello")
