#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

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


class StellaRepo(object):

    @classmethod
    def from_yaml(cls, yaml_path):
        yaml_file = yaml_path.open()
        yaml_dict = yaml.load(yaml_file, Loader=yaml.Loader)
        yaml_file.close()
        return cls(yaml_dict, yaml_path.parents[0])

    def __init__(self, stella_yaml_dict, project_path, project_name=None):

        self.project_name = project_name
        if 'name' in stella_yaml_dict.keys():
            self.project_name = stella_yaml_dict['name']

        self.project_path = project_path

        def path_from(path_string):
            p = self.project_path
            for piece in path_string.split('/'):
                p /= piece
            return p

        self.apps = []
        if 'apps' in stella_yaml_dict.keys():
            self.apps = [path_from(p) for p in stella_yaml_dict['apps']]

        self.build_static_lib = False
        if 'build-static-lib' in stella_yaml_dict.keys():
            self.build_static_lib = stella_yaml_dict['build-static-lib']

        self.build_shared_lib = False
        if 'build-shared-lib' in stella_yaml_dict.keys():
            self.build_shared_lib = stella_yaml_dict['build-shared-lib']

        self.dependencies = []
        if 'dependencies' in stella_yaml_dict.keys():
            self.dependencies = stella_yaml_dict['dependencies']

        self.private_header_paths = []
        if 'private-header-paths' in stella_yaml_dict.keys():
            self.private_header_paths = [path_from(p) for p in stella_yaml_dict['private-header-paths']]


        self.public_header_paths = []
        if 'public-header-paths' in stella_yaml_dict.keys():
            self.public_header_paths = [path_from(p) for p in stella_yaml_dict['public-header-paths']]

        self.sources = []
        if 'source-globs' in stella_yaml_dict.keys():
            for source_glob in stella_yaml_dict['source-globs']:
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
stella_path = pathlib.Path(sourcedir)
root_path = stella_path.parents[0]
stella_path = stella_path.relative_to(root_path)
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

stella_repo = StellaRepo.from_yaml(root_path / 'stella.yaml')

# Resolve dependencies
print(' 🎯 Resolving dependencies to build stella repo {}'.format(stella_repo.project_name))
inventory = [stella_repo.project_name]
remaining_dependencies = stella_repo.dependencies
print('\t 📚 We already have a clone of {} - of course'.format(stella_repo.project_name))
print('\t 📦 {} is a stella repo (of course), already loaded {}'.format(stella_repo.project_name, root_path / 'stella.yaml'))
for dep_dict in remaining_dependencies:
    print('\t 🧩 Discovered {}\'s dependency {}'.format(stella_repo.project_name, dep_dict['name']))
print('\t 🏁 {} acquired'.format(stella_repo.project_name))

while len(remaining_dependencies):

    dep_dict = remaining_dependencies.pop(0)
    dep_path = deps_path / dep_dict['name']

    print(' 🎯 Resolving dependency {}'.format(dep_dict['name']))

    if dep_path.exists():
        print('\t 📚 We already have a clone of {}'.format(dep_dict['name']))
    else:
        print('\t ⏳ Cloning {} from {} into {}'.format(dep_dict['name'], dep_dict['url'], dep_path))
        git_repo = git.Repo.clone_from(dep_dict['url'], str(dep_path))
        if 'checkout' in dep_dict.keys():
            git_repo.git.checkout(dep_dict['checkout'])

    dep_stella_yaml_path = dep_path / 'stella.yaml'
    if dep_stella_yaml_path.exists():
        print('\t 📦 {} is a stella repo as well; loading {}'.format(dep_dict['name'], dep_stella_yaml_path))
        dep = StellaRepo.from_yaml(dep_stella_yaml_path)
    elif 'stella-yaml' in dep_dict:
        print('\t 🍃 {} is not a stella repo; loading dependency info from within {} instead'.format(dep_dict['name'], root_path / 'stella.yaml'))
        dep = StellaRepo(dep_dict['stella-yaml'], dep_path, dep_dict['name'])
    else:
        print('\t 💣 Since cloned dependency "{}" does not have a stella.yaml file, you need the key "stella-yaml" in '\
              'the dependency to map to an inline stella.yaml object, so we know what to do with those files'.format(dep_dict['name']))
        sys.exit(1)

    if dep.project_name in inventory:
        print('\t ✅ Already covered {}'.format(dep.project_name))
        continue

    stella_repo.private_header_paths += dep.private_header_paths
    stella_repo.private_header_paths += dep.public_header_paths
    stella_repo.sources += dep.sources

    for sub_dependency in dep.dependencies:
        if sub_dependency['name'] not in inventory and sub_dependency not in remaining_dependencies:
            print('\t 🧩 Discovered {}\'s dependency {}'.format(dep.project_name, sub_dependency['name']))
            remaining_dependencies.append(sub_dependency)
        else:
            print('\t ✅ Already covered {}\'s dependency {}'.format(dep.project_name, sub_dependency['name']))

    print('\t 🏁 {} acquired'.format(dep.project_name))
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
compiler_flags += ' '.join(['-I{}'.format(x) for x in stella_repo.public_header_paths + stella_repo.private_header_paths])
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
for this_src_path in filter(lambda s: s not in stella_repo.apps, stella_repo.sources):
    this_obj_path = obj_path / this_src_path
    this_obj_name = str(this_obj_path)+'.o'
    obj_names.append(this_obj_name)
    ninja.build(this_obj_name, 'compile_static', str(this_src_path))
    this_fpic_obj_name = str(this_obj_path)+'.fPIC.o'
    fpic_obj_names.append(this_fpic_obj_name)
    ninja.build(this_fpic_obj_name, 'compile_fpic', str(this_src_path))
ninja.newline()

ninja.comment('Build a static library from the static objects')
static_lib_name = str(lib_path / 'lib{}.a'.format(stella_repo.project_name))
ninja.build(static_lib_name, 'link_static', obj_names)
ninja.newline()
if stella_repo.build_static_lib:
    ninja.default(static_lib_name)

ninja.comment('Build a shared library from the fPIC objects')
shared_lib_name = str(lib_path / 'lib{}.so'.format(stella_repo.project_name))
ninja.build(shared_lib_name, 'link_shared', fpic_obj_names)
ninja.newline()
if stella_repo.build_shared_lib:
    ninja.default(shared_lib_name)

ninja.comment('Build executables for the apps')
for app_source in stella_repo.apps:
    app_obj_path = obj_path / app_source
    app_obj_name = str(app_obj_path)+'.o'
    ninja.build(app_obj_name, 'compile_static', str(app_source))
    executable_name = str(bin_path / app_source.stem)
    ninja.build(executable_name, 'compile_exe', obj_names+[app_obj_name])
    ninja.default(executable_name)
ninja.newline()

ninja.comment('Copy the public header files into the build products')
for public_header_path in stella_repo.public_header_paths:
    for header_file_path in public_header_path.glob('**/*'):
        header_file_src = str(header_file_path)
        if len(stella_repo.public_header_paths) == 1:
            header_file_dst = str(inc_path / header_file_path.relative_to(public_header_path))
        else:
            header_file_dst = str(inc_path / header_file_path.relative_to(stella_repo.project_path))
        ninja.build(header_file_dst, 'copy_file', header_file_src)
        if stella_repo.build_static_lib or stella_repo.build_shared_lib:
            ninja.default(header_file_dst)
ninja.newline()

ninja.close()
