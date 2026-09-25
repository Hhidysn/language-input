rem Build environment for GitHub's windows-2022 runner and local VS 2022.
set WEASEL_ROOT=%CD%
if not defined BOOST_ROOT set BOOST_ROOT=%WEASEL_ROOT%\deps\boost_1_84_0
set BJAM_TOOLSET=msvc-14.3
set CMAKE_GENERATOR="Visual Studio 17 2022"
set PLATFORM_TOOLSET=v143
