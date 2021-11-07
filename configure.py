#!/usr/bin/env python3

# TODO add a monolithic build (all sources in one compile command)
# TODO add a 1TU build (generated source file that includes all others)
# TODO platform-specific flags and suffixes for making shared objects

import sys
import os
import argparse

import yaml
import pathlib
import git

import ninja_syntax


sourcedir = os.path.dirname(os.path.realpath(__file__))


class Clump(object):

    @classmethod
    def from_yaml(cls, yaml_path):
        clump_file = yaml_path.open()
        clump_dict = yaml.load(clump_file, Loader=yaml.Loader)
        clump_file.close()
        return cls(clump_dict, yaml_path.parents[0])

    def __init__(self, clump_dict, project_path, project_name=None):

        self.project_name = project_name
        if 'name' in clump_dict.keys():
            self.project_name = clump_dict['name']

        self.project_path = project_path

        def path_from(path_string):
            p = self.project_path
            for piece in path_string.split('/'):
                p /= piece
            return p

        self.apps = []
        if 'apps' in clump_dict.keys():
            self.apps = [path_from(p) for p in clump_dict['apps']]

        self.build_static_lib = False
        if 'build-static-lib' in clump_dict.keys():
            self.build_static_lib = clump_dict['build-static-lib']

        self.build_shared_lib = False
        if 'build-shared-lib' in clump_dict.keys():
            self.build_shared_lib = clump_dict['build-shared-lib']

        self.dependencies = []
        if 'dependencies' in clump_dict.keys():
            self.dependencies = clump_dict['dependencies']

        self.private_header_paths = []
        if 'private-header-paths' in clump_dict.keys():
            self.private_header_paths = [path_from(p) for p in clump_dict['private-header-paths']]


        self.public_header_paths = []
        if 'public-header-paths' in clump_dict.keys():
            self.public_header_paths = [path_from(p) for p in clump_dict['public-header-paths']]

        self.sources = []
        if 'source-globs' in clump_dict.keys():
            for source_glob in clump_dict['source-globs']:
                self.sources += [p for p in self.project_path.glob(source_glob) if not p.is_dir()]
                # TODO glob might have pathsep problems on windows

# Parse command-line arguments
parser = argparse.ArgumentParser(description='Generate ninja.build')
parser.add_argument('--config', metavar='CONFIG', type=str, help='The build configuration, either "release" or "debug"')
parser.add_argument('--env', metavar='ENV_YAML', type=str, help='Override the platform-default build environment YAML blob')
parser.add_argument('--method', metavar='METHOD', type=str, help='Override incremental build with "monolithic" or "1TU" builds')
args = parser.parse_args()
if not args.config:
    args.config = 'release'
if args.config not in ['release', 'debug']:
    print('Config must be either "release" or "debug"')
    sys.exit(1)

# Understand the file system around us
clumps_path = pathlib.Path(sourcedir)
root_path = clumps_path.parents[0]
clumps_path = clumps_path.relative_to(root_path)
if pathlib.Path('.').absolute() != root_path.absolute():
    print('Error: configure.py must be run from the root of the project directory')
    exit(1)
root_path = pathlib.Path('.')

# Define the structure of the build tree
deps_path = root_path / 'deps'
deps_path.mkdir(exist_ok=True)
build_path = root_path / 'build'
build_path.mkdir(exist_ok=True)
bin_path = build_path / 'bin'
bin_path.mkdir(exist_ok=True)
lib_path = build_path / 'lib'
lib_path.mkdir(exist_ok=True)
obj_path = build_path / 'obj'
obj_path.mkdir(exist_ok=True)
inc_path = build_path / 'include'
inc_path.mkdir(exist_ok=True)

clump = Clump.from_yaml(root_path / 'clump.yaml')

# Resolve dependencies
print(' 🎯 Resolving dependencies to build {}'.format(clump.project_name))
inventory = [clump.project_name]
remaining_dependencies = clump.dependencies
for dep_dict in remaining_dependencies:
    print('\t 🔍 Discovered dependency {}'.format(dep_dict['name']))
while len(remaining_dependencies):
    dep_dict = remaining_dependencies[0]
    remaining_dependencies = remaining_dependencies[1:]
    dep_path = deps_path / dep_dict['name']
    if not dep_path.exists():
        print('\t ⬇️ Cloning {}'.format(dep_dict['name']), end='\r')
        repo = git.Repo.clone_from(dep_dict['url'], str(dep_path))
        print(end='\x1b[1K\r')
        print('\t ✅ Cloned {}'.format(dep_dict['name']))
        # TODO
        # if 'checkout' in dep_dict:
        #     repo.heads[dep_dict['checkout']].checkout()
    dep_clump_yaml_path = dep_path / 'clump.yaml'
    dep = None
    if 'clump' in dep_dict:
        dep = Clump(dep_dict['clump'], dep_path, dep_dict['name'])
    if dep_clump_yaml_path.exists():
        dep = Clump.from_yaml(dep_clump_yaml_path)
    # TODO what if dep is None now?
    if dep.project_name in inventory:
        print('\t ✅ Already covered {}'.format(dep.project_name))
        continue
    clump.private_header_paths += dep.private_header_paths
    clump.private_header_paths += dep.public_header_paths
    clump.sources += dep.sources
    for sub_dependency in dep.dependencies:
        print('\t 🔍 Discovered {}\'s dependency {}'.format(dep.project_name, sub_dependency['name']))
        if sub_dependency['name'] not in inventory:
            remaining_dependencies = [sub_dependency] + remaining_dependencies
        else:
            print('\t ✅ Already covered {}'.format(sub_dependency['name']))
    inventory.append(dep.project_name)

print()

# Understand the system's build tools, or accept an override
system_default_build_environments = {
    'freebsd': {'compiler': 'g++', 'linker': 'ld', 'archiver': 'ar', 'copier': 'cp'},
    'linux': {'compiler': 'g++', 'linker': 'ld', 'archiver': 'ar', 'copier': 'cp'},
    'cygwin': {'compiler': 'g++', 'linker': 'ld', 'archiver': 'ar', 'copier': 'cp'},
    'darwin': { 'compiler': 'clang++', 'linker': 'ld', 'archiver': 'ar', 'copier': 'cp'},
} # TODO aix, win32
build_environment = None
if sys.platform in system_default_build_environments.keys():
    build_environment = system_default_build_environments[sys.platform]
elif not args.env:
    print('platform "{}" not known to this script, and no override build environment provided')
    sys.exit(1)
if args.env:
    env_file = open(args.env)
    build_environment = yaml.load(env_file, Loader=yaml.Loader)
    env_file.close()

ninja = ninja_syntax.Writer(open(root_path / 'build.ninja', 'w'))
ninja.comment('Generated file - do not edit!')
ninja.newline()

ninja.comment('Required tool locations on the build platform')
ninja.variable('cxx', build_environment['compiler'])
ninja.variable('ld', build_environment['linker'])
ninja.variable('ar', build_environment['archiver'])
ninja.variable('cp', build_environment['copier'])
ninja.newline()

ninja.comment('Tool flags variables')
compiler_flags = '-std=c++17 -Wall -Wextra -Wno-unused-parameter -Werror -pedantic'
if args.config == 'release':
    compiler_flags += ' -O3 -flto '
elif args.config == 'debug':
    compiler_flags += ' -g '
compiler_flags += ' '.join(['-I{}'.format(x) for x in clump.public_header_paths + clump.private_header_paths])
ninja.variable('cxxflags', compiler_flags)
ninja.variable('arflags', '-rcs')
ninja.newline()

ninja.comment('Build rule definitions')
ninja.rule('compile_exe', '$cxx -MD -MF $out.d $cxxflags $in -o $out', depfile='$out.d')
ninja.rule('compile_static', '$cxx -MD -MF $out.d $cxxflags -c $in -o $out', depfile='$out.d')
ninja.rule('compile_fpic', '$cxx -MD -MF $out.d $cxxflags -c -fPIC $in -o $out', depfile='$out.d')
ninja.rule('link_static', '$ar $arflags $out $in', depfile='$out.d')
ninja.rule('link_shared', '$cxx -MD -MF $out.d $cxxflags -shared -o $out $in', depfile='$out.d')
ninja.rule('copy_file', '$cp $in $out')
ninja.newline()

ninja.comment('Build static and fPIC objects from sources, except apps')
obj_names = []
fpic_obj_names = []
for this_src_path in filter(lambda s: s not in clump.apps, clump.sources):
    this_obj_path = obj_path / this_src_path
    this_obj_name = str(this_obj_path)+'.o'
    obj_names.append(this_obj_name)
    ninja.build(this_obj_name, 'compile_static', str(this_src_path))
    this_fpic_obj_name = str(this_obj_path)+'.fPIC.o'
    fpic_obj_names.append(this_fpic_obj_name)
    ninja.build(this_fpic_obj_name, 'compile_fpic', str(this_src_path))
ninja.newline()

ninja.comment('Build a static library from the static objects')
static_lib_name = str(lib_path / 'lib{}.a'.format(clump.project_name))
ninja.build(static_lib_name, 'link_static', obj_names)
ninja.newline()
if clump.build_static_lib:
    ninja.default(static_lib_name)

ninja.comment('Build a shared library from the fPIC objects')
shared_lib_name = str(lib_path / 'lib{}.so'.format(clump.project_name))
ninja.build(shared_lib_name, 'link_shared', fpic_obj_names)
ninja.newline()
if clump.build_shared_lib:
    ninja.default(shared_lib_name)

ninja.comment('Build executables for the apps')
for app_source in clump.apps:
    app_obj_path = obj_path / app_source
    app_obj_name = str(app_obj_path)+'.o'
    ninja.build(app_obj_name, 'compile_static', str(app_source))
    executable_name = str(bin_path / app_source.stem)
    ninja.build(executable_name, 'compile_exe', obj_names+[app_obj_name])
    ninja.default(executable_name)
ninja.newline()

ninja.comment('Copy the public header files into the build products')
for public_header_path in clump.public_header_paths:
    for header_file_path in public_header_path.glob('**/*'):
        header_file_src = str(header_file_path)
        if len(clump.public_header_paths) == 1:
            header_file_dst = str(inc_path / header_file_path.relative_to(public_header_path))
        else:
            header_file_dst = str(inc_path / header_file_path.relative_to(clump.project_path))
        ninja.build(header_file_dst, 'copy_file', header_file_src)
        if clump.build_static_lib or clump.build_shared_lib:
            ninja.default(header_file_dst)
ninja.newline()

ninja.close()
