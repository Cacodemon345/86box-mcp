# Loaded via CMAKE_PROJECT_86Box_INCLUDE (only for the 86Box project, not CMake's
# compiler probes) in the Windows cross build.
# 86Box's install() rules call Qt's windeployqt, which Fedora's MinGW Qt doesn't
# provide. We never run install (package.py collects the DLLs), so a placeholder
# target satisfies the generator expression.
if(NOT TARGET Qt6::windeployqt)
    add_executable(Qt6::windeployqt IMPORTED GLOBAL)
    set_target_properties(Qt6::windeployqt PROPERTIES IMPORTED_LOCATION /usr/bin/true)
endif()

# qt_main.cpp calls timeBeginPeriod()/timeEndPeriod(). MSYS2's static SDL pulls in
# winmm implicitly; Fedora's shared SDL3 doesn't, so link it explicitly.
link_libraries(winmm)
