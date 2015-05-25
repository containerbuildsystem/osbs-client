%global with_python3 0

%global commit e52e756028d2b6acd1ef3d2ce15c73d585db06d4
%global shortcommit %(c=%{commit}; echo ${c:0:7})
# set to 0 to create a normal release
%global postrelease 0
%global release 1

Name:           osbs
Version:        0.8
%if "x%{postrelease}" != "x0"
Release:        %{release}.%{postrelease}.git.%{shortcommit}%{?dist}
%else
Release:        %{release}%{?dist}
%endif

Summary:        Python command line client for OpenShift Build Service
Group:          Development/Tools
License:        BSD
URL:            https://github.com/DBuildService/osbs
Source0:        https://github.com/DBuildService/osbs/archive/%{commit}/osbs-%{commit}.tar.gz

BuildArch:      noarch

Requires:       python-osbs

BuildRequires:  python2-devel
BuildRequires:  python-setuptools

%if 0%{?with_python3}
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
%endif

%description
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs command line client.

%package -n python-osbs
Summary:        Python 2 module for OpenShift Build Service
Group:          Development/Tools
License:        BSD
Requires:       python-pycurl
Requires:       python-setuptools
#Requires:       python-requests

%description -n python-osbs
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs Python 2 bindings.

%if 0%{?with_python3}
%package -n python3-osbs
Summary:        Python 3 module for OpenShift Build Service
Group:          Development/Tools
License:        BSD
Requires:       python3-pycurl
Requires:       python3-setuptools
#Requires:       python3-requests

%description -n python3-osbs
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs Python 3 bindings.
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
mv %{buildroot}%{_bindir}/osbs %{buildroot}%{_bindir}/osbs3
%endif # with_python3

%{__python} setup.py install --skip-build --root %{buildroot}
mv %{buildroot}%{_bindir}/osbs %{buildroot}%{_bindir}/osbs2
ln -s  %{_bindir}/osbs2 %{buildroot}%{_bindir}/osbs


%files
%doc README.md
%{_bindir}/osbs


%files -n python-osbs
%doc README.md
%{!?_licensedir:%global license %%doc}
%license LICENSE
%{_bindir}/osbs2
%if 0%{?rhel}
%{python_sitelib}/osbs/
%{python_sitelib}/osbs-%{version}-py2.*.egg-info/
%else
%{python2_sitelib}/osbs/
%{python2_sitelib}/osbs-%{version}-py2.*.egg-info/
%endif
%dir %{_datadir}/osbs
%{_datadir}/osbs/*.json


%if 0%{?with_python3}
%files -n python3-osbs
%doc README.md
%{!?_licensedir:%global license %%doc}
%license LICENSE
%{_bindir}/osbs3
%{python3_sitelib}/osbs/
%{python3_sitelib}/osbs-%{version}-py3.*.egg-info/
%dir %{_datadir}/osbs
%{_datadir}/osbs/*.json
%endif # with_python3

%changelog
* Mon May 25 2015 Jiri Popelka <jpopelka@redhat.com> - 0.8-1
- new upstream release: 0.8

* Fri May 22 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.7-1
- new upstream release: 0.7

* Thu May 21 2015 Jiri Popelka <jpopelka@redhat.com> - 0.6-2
- fix %%license handling

* Thu May 21 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.6-1
- new upstream release: 0.6

* Tue May 19 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.5-1
- new upstream release: 0.5

* Tue May 12 2015 Slavek Kabrda <bkabrda@redhat.com> - 0.4-2
- Introduce python-osbs subpackage
- move /usr/bin/osbs to /usr/bin/osbs2, /usr/bin/osbs is now a symlink
- depend on python[3]-setuptools because of entrypoints usage

* Tue Apr 21 2015 Martin Milata <mmilata@redhat.com> - 0.4-1
- new upstream release

* Wed Apr 15 2015 Martin Milata <mmilata@redhat.com> - 0.3-1
- new upstream release

* Wed Apr 08 2015 Martin Milata <mmilata@redhat.com> - 0.2-2.c1216ba
- update to c1216ba

* Tue Apr 07 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.2-1
- new upstream release

* Tue Mar 24 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-4
- update to 758648c8

* Thu Mar 19 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-3
- no need to require also python-requests

* Thu Mar 19 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-2
- separate executable for python 3

* Wed Mar 18 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-1
- initial spec
