%global with_python3 0

%global commit e073d7cc6cf7a07eafa00e2d44a654a040640390
%global shortcommit %(c=%{commit}; echo ${c:0:7})

Name:           osbs
Version:        0.1
Release:        2%{?dist}

Summary:        Python module and command line client for OpenShift Build Service
Group:          Development/Tools
License:        BSD
URL:            https://github.com/DBuildService/osbs
Source0:        https://github.com/DBuildService/osbs/archive/%{commit}/osbs-%{commit}.tar.gz

BuildArch:      noarch

BuildRequires:  python2-devel
BuildRequires:  python-setuptools

%if 0%{?with_python3}
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
%endif

Requires:       python-pycurl
Requires:       python-requests

%description
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
All of this can be done from command line and from python.

%if 0%{?with_python3}
%package -n python3-osbs
Summary:        Python module and command line client for OpenShift Build Service
Group:          Development/Tools
License:        BSD
Requires:       python3-pycurl
Requires:       python3-requests

%description -n python3-osbs
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
All of this can be done from command line and from python.
%endif # with_python3


%prep
%setup -qn osbs-%{commit}

%if 0%{?with_python3}
rm -rf %{py3dir}
cp -a . %{py3dir}
find %{py3dir} -name '*.py' | xargs sed -i '1s|^#!python|#!%{__python3}|'
%endif # with_python3


%build
# build python package
%{__python} setup.py build

%if 0%{?with_python3}
pushd %{py3dir}
%{__python3} setup.py build
popd
%endif # with_python3


%install
%if 0%{?with_python3}
pushd %{py3dir}
%{__python3} setup.py install --skip-build --root %{buildroot}
popd
pushd %{buildroot}%{_bindir}
mv osbs osbs3
popd
%endif # with_python3

%{__python} setup.py install --skip-build --root %{buildroot}


%files
%doc README.md
%license LICENSE
%{_bindir}/osbs
%{python2_sitelib}/osbs/
%{python2_sitelib}/osbs-%{version}-py2.*.egg-info/
%dir %{_datadir}/osbs
%{_datadir}/osbs/*.json


%if 0%{?with_python3}
%files -n python3-osbs
%doc README.md
%license LICENSE
%{_bindir}/osbs3
%{python3_sitelib}/osbs/
%{python3_sitelib}/osbs-%{version}-py3.*.egg-info/
%dir %{_datadir}/osbs
%{_datadir}/osbs/*.json
%endif # with_python3

%changelog
* Thu Mar 19 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-2
- separate executable for python 3

* Wed Mar 18 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-1
- initial spec
