Name:           buildbench-hello
Version:        1.0
Release:        1
%global __spec_install_post /bin/true
%global __spec_post_trans /bin/true
%global __spec_pre_trans /bin/true
%global __spec_post_trans /bin/true
Summary:        Minimal package for the Build-Bench Starter Kit
License:        MIT
BuildArch:      noarch

%description
A minimal package used by the trusted Build-Bench one-command demo.

%prep

%build
# Build step intentionally empty

%install
mkdir -p %{buildroot}%{_datadir}/buildbench-hello
echo "Hello from Build-Bench" > %{buildroot}%{_datadir}/buildbench-hello/hello.txt

%global __spec_pre_install /bin/true
%global __spec_post_install /bin/true
%files
%dir %{_datadir}/buildbench-hello
%{_datadir}/buildbench-hello/hello.txt
