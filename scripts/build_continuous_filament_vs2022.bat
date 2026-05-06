@echo off
setlocal

set ROOT=%~dp0..
pushd "%ROOT%"

set BUILD_TYPE=RelWithDebInfo
set BUILD_DIR=build-dbginfo
set DEPS_BUILD_DIR=deps\build-dbginfo

if "%1"=="" goto all
if /I "%1"=="deps" goto deps
if /I "%1"=="slicer" goto slicer
if /I "%1"=="configure" goto configure
if /I "%1"=="clean-status" goto clean_status

echo Usage:
echo   scripts\build_continuous_filament_vs2022.bat configure
echo   scripts\build_continuous_filament_vs2022.bat deps
echo   scripts\build_continuous_filament_vs2022.bat slicer
echo   scripts\build_continuous_filament_vs2022.bat
popd
exit /b 1

:configure
cmake -S deps -B "%DEPS_BUILD_DIR%" -G "Visual Studio 17 2022" -A x64 -DCMAKE_BUILD_TYPE=%BUILD_TYPE%
if errorlevel 1 goto fail
cmake -S . -B "%BUILD_DIR%" -G "Visual Studio 17 2022" -A x64 -DORCA_TOOLS=ON -DCMAKE_BUILD_TYPE=%BUILD_TYPE%
if errorlevel 1 goto fail
goto done

:deps
cmake -S deps -B "%DEPS_BUILD_DIR%" -G "Visual Studio 17 2022" -A x64 -DCMAKE_BUILD_TYPE=%BUILD_TYPE%
if errorlevel 1 goto fail
cmake --build "%DEPS_BUILD_DIR%" --config %BUILD_TYPE% --target deps -- /m
if errorlevel 1 goto fail
goto done

:slicer
cmake -S . -B "%BUILD_DIR%" -G "Visual Studio 17 2022" -A x64 -DORCA_TOOLS=ON -DCMAKE_BUILD_TYPE=%BUILD_TYPE%
if errorlevel 1 goto fail
cmake --build "%BUILD_DIR%" --config %BUILD_TYPE% --target ALL_BUILD -- /m
if errorlevel 1 goto fail
goto done

:all
call "%~f0" deps
if errorlevel 1 goto fail
call "%~f0" slicer
if errorlevel 1 goto fail
goto done

:clean_status
cmake --build "%BUILD_DIR%" --config %BUILD_TYPE% --target help
goto done

:fail
echo.
echo Build helper failed. Check the error above.
popd
exit /b 1

:done
popd
exit /b 0
