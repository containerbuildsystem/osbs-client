%if (0%{?fedora} >= 23 || 0%{?rhel} >= 8)
%global with_python3 1
%endif

%if 0%{?fedora}
# rhel/epel has no flexmock, pytest-capturelog
%global with_check 1
%endif

%global commit 49ef2c5d631b8a0c5f82a1d9354e6f7271ba5f12
%global shortcommit %(c=%{commit}; echo ${c:0:7})
# set to 0 to create a normal release
%global postrelease 0
%global release 2

%global osbs_obsolete_vr 0.14-2

Name:           osbs-client
Version:        0.15
%if "x%{postrelease}" != "x0"
Release:        %{release}.%{postrelease}.git.%{shortcommit}%{?dist}
%else
Release:        %{release}%{?dist}
%endif

Summary:        Python command line client for OpenShift Build Service
Group:          Development/Tools
License:        BSD
URL:            https://github.com/projectatomic/osbs-client
Source0:        https://github.com/projectatomic/osbs-client/archive/%{commit}/osbs-client-%{commit}.tar.gz

BuildArch:      noarch

Requires:       python-osbs-client = %{version}-%{release}

BuildRequires:  python2-devel
BuildRequires:  python-setuptools
%if 0%{?with_check}
BuildRequires:  pytest
BuildRequires:  python-pytest-capturelog
BuildRequires:  python-flexmock
BuildRequires:  python-six
BuildRequires:  python-dockerfile-parse
BuildRequires:  python-pycurl
%endif # with_check

%if 0%{?with_python3}
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
%if 0%{?with_check}
BuildRequires:  python3-pytest
BuildRequires:  python3-pytest-capturelog
BuildRequires:  python3-flexmock
BuildRequires:  python3-six
BuildRequires:  python3-dockerfile-parse
BuildRequires:  python3-pycurl
%endif # with_check
%endif # with_python3


Provides:       osbs = %{version}-%{release}
Obsoletes:      osbs < %{osbs_obsolete_vr}

%description
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs command line client.

%package -n python-osbs-client
Summary:        Python 2 module for OpenShift Build Service
Group:          Development/Tools
License:        BSD
Requires:       python-dockerfile-parse
Requires:       python-pycurl
Requires:       python-setuptools
Requires:       krb5-workstation

%if 0%{?rhel} && 0%{?rhel} <= 6
Requires:       python-argparse
%endif

Provides:       python-osbs = %{version}-%{release}
Obsoletes:      python-osbs < %{osbs_obsolete_vr}

%description -n python-osbs-client
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs Python 2 bindings.

%if 0%{?with_python3}
%package -n python3-osbs-client
Summary:        Python 3 module for OpenShift Build Service
Group:          Development/Tools
License:        BSD
Requires:       python3-dockerfile-parse
Requires:       python3-pycurl
Requires:       python3-dateutil
Requires:       python3-setuptools
Requires:       krb5-workstation

Provides:       python3-osbs = %{version}-%{release}
Obsoletes:      python3-osbs < %{osbs_obsolete_vr}

%description -n python3-osbs-client
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs Python 3 bindings.
%endif # with_python3


%prep
%setup -qn %{name}-%{commit}

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


%if 0%{?with_check}
%check
%if 0%{?with_python3}
py.test-3 -vv tests
%endif # with_python3

py.test-2 -vv tests
%endif # with_check


%files
%doc README.md
%{_bindir}/osbs


%files -n python-osbs-client
%doc README.md
%{!?_licensedir:%global license %%doc}
%license LICENSE
%{_bindir}/osbs2
%if 0%{?rhel}
%{python_sitelib}/osbs*
%else
%{python2_sitelib}/osbs*
%endif
%dir %{_datadir}/osbs
%{_datadir}/osbs/*.json


%if 0%{?with_python3}
%files -n python3-osbs-client
%doc README.md
%{!?_licensedir:%global license %%doc}
%license LICENSE
%{_bindir}/osbs3
%{python3_sitelib}/osbs*
%dir %{_datadir}/osbs
%{_datadir}/osbs/*.json
%endif # with_python3

%changelog
* Thu Nov 05 2015 Jiri Popelka <jpopelka@redhat.com> - 0.15-2
- build for Python 3
- %%check section

* Mon Oct 19 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.15-1
- new upstream release: 0.15

* Thu Aug 06 2015 bkabrda <bkabrda@redhat.com> - 0.14-2
- renamed to osbs-client

* Wed Jul 01 2015 Martin Milata <mmilata@redhat.com> - 0.14-1
- new upstream release: 0.14

* Fri Jun 12 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.13.1-1
- new fixup upstream release: 0.13.1

* Fri Jun 12 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.13-1
- new upstream release: 0.13

* Wed Jun 10 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.12-1
- new upstream release: 0.12

* Wed Jun 03 2015 Martin Milata <mmilata@redhat.com> - 0.11-1
- new upstream release: 0.11

* Thu May 28 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.10-1
- new upstream release: 0.10

* Thu May 28 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.9-1
- new upstream release: 0.9

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
