"""
Copyright (c) 2020 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import, unicode_literals

import six

from osbs.exceptions import OsbsValidationException


class BuildParam(object):
    """
    One parameter of a spec.

    Works like a data descriptor (similar to `property`), should be defined as
    a class attribute on classes that inherit from BuildParamsBase.

    When defined as a class attribute, the name of the attribute should match
    the name given to the BuildParam in __init__(), e.g.:

    >>> class BuildParams(BuildParamsBase)
    >>>     x = BuildParam("x")
    >>>     y = BuildParam("y")

    BuildParamsBase and classes that inherit from it will validate this during
    class creation.
    """

    def __init__(self, name, default=None, required=False, include_in_json=True):
        """
        Define a BuildParam.

        Do NOT use mutable values as default, same issues apply as when using
        mutable defaults for method params.

        :param name: str, name of param, should match name of class attribute
        :param default: any immutable object, default value of param
        :param required: bool, is the param allowed to be None?
        :param include_in_json: bool, include the param in to_json() output?
        """
        self._name = name
        self._default = default
        self._required = required
        self._include_in_json = include_in_json
        self._mangled_name = "_{self.__class__.__name__}__{self.name}".format(self=self)

    @property
    def name(self):
        return self._name

    @property
    def required(self):
        return self._required

    @property
    def include_in_json(self):
        return self._include_in_json

    def __repr__(self):
        return "{self.__class__.__name__}({self.name!r})".format(self=self)

    def __get__(self, obj, objtype=None):
        try:
            # Bypass potential __getattr__ redefinition in class
            return object.__getattribute__(obj, self._mangled_name)
        except AttributeError:
            return self._default

    def __set__(self, obj, value):
        # Bypass potential __setattr__ redefinition in class
        object.__setattr__(obj, self._mangled_name, value)


class BuildParamsMeta(type):
    """
    Metaclass for BuildParams
    """

    def __new__(cls, name, bases, namespace):  # pylint: disable=bad-mcs-classmethod-argument
        """
        Create a new BuildParams class. Collect all BuildParam attributes
        from class namespace, check that their names match the attribute names
        and give the class a __params_dict__ attribute.
        """
        pdict = {
            attr_name: attr for attr_name, attr in namespace.items()
            if isinstance(attr, BuildParam)
        }
        for param_name, param in pdict.items():
            if param_name != param.name:
                raise TypeError("Mismatched param name: {} = {!r}".format(param_name, param))
        namespace["__params_dict__"] = pdict
        return super(BuildParamsMeta, cls).__new__(cls, name, bases, namespace)

    @property
    def params_dict(cls):
        """
        Get dict of {param.name: param} for all params defined in class and any
        of its parent classes.

        Respects MRO (if child class redefines a param, returns the child param,
        not the parent one).
        """
        pdict = {}
        for cls_or_base in reversed(cls.__mro__):
            pdict.update(getattr(cls_or_base, "__params_dict__", {}))
        return pdict

    def get_param(cls, name):
        """
        Get BuildParam instance defined on class or any of its parents by name.

        May be preferrable over cls.params_dict.get(name) because this does not
        construct an entire params dictionary.
        """
        for cls_or_base in cls.__mro__:
            param = getattr(cls_or_base, "__params_dict__", {}).get(name)
            if param is not None:
                return param
        return None

    @property
    def params(cls):
        """
        Get all params for a class
        """
        # pylint: disable=no-member; pylint does not understand metaclass properties
        return sorted(cls.params_dict.values(), key=lambda param: param.name)

    @property
    def required_params(cls):
        """
        Get all required params for a class
        """
        # pylint: disable=not-an-iterable; pylint does not understand metaclass properties
        return [p for p in cls.params if p.required]


@six.add_metaclass(BuildParamsMeta)
class BuildParamsBase(object):
    """
    Base class for BuildParams
    """

    def __init__(self, **kwargs):
        """
        Set all params from keyword arguments, fail if any are unknown
        """
        pdict = self.__class__.params_dict
        unexpected = set(kwargs) - set(pdict)
        if unexpected:
            unexpected_repr = ", ".join(repr(pname) for pname in sorted(unexpected))
            raise OsbsValidationException("Got unexpected params: {}".format(unexpected_repr))
        for name, value in kwargs.items():
            pdict[name].__set__(self, value)

    def __repr__(self):
        params_repr = ", ".join(
            "{}={!r}".format(p.name, p.__get__(self)) for p in self.__class__.params
        )
        return "{}({})".format(self.__class__.__name__, params_repr)

    def __setattr__(self, name, value):
        """
        Set attribute only if it is defined as a BuildParam on this class (or parent)
        """
        param = self.__class__.get_param(name)
        if param is not None:
            param.__set__(self, value)
        else:
            raise AttributeError("No such param: {!r}".format(name))
