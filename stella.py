#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

# TODO add a monolithic build (all sources in one compile command)
# TODO add a 1TU build (generated source file that includes all others)
# TODO platform-specific flags and suffixes for making shared objects
# TODO our use of glob might have pathsep problems on windows
# TODO get compiler flags for each object from its home repository's stella.yaml
# TODO use a read and validated object instead of passing dep_dict
# TODO it only makes sense to call check_dependencies after resolve_dependencies

import sys
import os
import argparse
import yaml
import pathlib
import git
import ninja_syntax


stella_path = pathlib.Path(os.path.dirname(os.path.realpath(__file__)))
root_path = stella_path.parents[0]
stella_path = stella_path.relative_to(root_path)
root_path = pathlib.Path('.')
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
test_path = root_path / 'test'
test_inc_path = test_path / 'include'
gtest_inc_path = stella_path / 'googletest' / 'googletest' / 'include'
gtest_lib_path = stella_path / 'googletest' / 'build' / 'lib'
test_target = str(bin_path / "run-tests")


def get_build_environment(env_override=None):

    system_default_build_environments = {
        'freebsd': {'compiler': 'g++', 'linker': 'ld', 'archiver': 'ar', 'copier': 'cp', 'cxxflags': ''},
        'linux': {'compiler': 'g++', 'linker': 'ld', 'archiver': 'ar', 'copier': 'cp', 'cxxflags': '-pthread'},
        'cygwin': {'compiler': 'g++', 'linker': 'ld', 'archiver': 'ar', 'copier': 'cp', 'cxxflags': ''},
        'darwin': { 'compiler': 'clang++', 'linker': 'ld', 'archiver': 'ar', 'copier': 'cp', 'cxxflags': ''},
    } # TODO aix, win32

    build_environment = None

    if sys.platform in system_default_build_environments.keys():
        build_environment = system_default_build_environments[sys.platform]

    if env_override:
        env_file = open(args.env)
        build_environment = yaml.load(env_file, Loader=yaml.Loader)
        env_file.close()

    return build_environment


class Source(object):

    def __init__(self, source_path):

        self.source_path = source_path
        self.object_path = obj_path / self.source_path
        self.source_name = str(self.source_path)
        self.object_name = str(self.object_path)+'.o'
        self.fpic_object_name = str(self.object_path)+'.fPIC.o'
        self.executable_name = str(bin_path / source_path.stem)


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
            for source_path_string in stella_yaml_dict['apps']:
                self.apps.append(Source(path_from(source_path_string)))

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
            for header_path_string in stella_yaml_dict['private-header-paths']:
                self.private_header_paths.append(path_from(header_path_string))

        self.public_header_paths = []
        if 'public-header-paths' in stella_yaml_dict.keys():
            for header_path_string in stella_yaml_dict['public-header-paths']:
                self.public_header_paths.append(path_from(header_path_string))

        self.sources = []
        if 'source-globs' in stella_yaml_dict.keys():
            for source_glob in stella_yaml_dict['source-globs']:
                for source_path in self.project_path.glob(source_glob):
                    if not source_path.is_dir() and not source_path in self.apps:
                        self.sources.append(Source(source_path))

        self.tests = []
        if 'test-globs' in stella_yaml_dict.keys():
            for test_glob in stella_yaml_dict['test-globs']:
                for source_path in self.project_path.glob(test_glob):
                    if not source_path.is_dir():
                        self.tests.append(Source(source_path))

        self.test_header_paths = []
        if 'test-header-paths' in stella_yaml_dict.keys():
            for header_path_string in stella_yaml_dict['test-header-paths']:
                self.test_header_paths.append(path_from(header_path_string))

    def resolve_dependencies(self):

        unresolved_dependencies = [x for x in self.dependencies]
        resolved_dependency_names = [self.project_name]

        print(' 🎯 Resolving dependencies to build stella repo {}'.format(self.project_name))
        print('\t 📚 We already have a clone of {} - of course'.format(self.project_name))
        print('\t 📦 {} is a stella repo (of course), already loaded {}'.format(self.project_name, root_path / 'stella.yaml'))
        for dep_dict in unresolved_dependencies:
            print('\t 🧩 Discovered {}\'s dependency {}'.format(self.project_name, dep_dict['name']))
        print('\t 🏁 {} acquired'.format(self.project_name))

        while len(unresolved_dependencies):

            dep_dict = unresolved_dependencies.pop(0)
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
                print('\t 📦 {} has a stella file at: {}'.format(dep_dict['name'], dep_stella_yaml_path))
                dep = StellaRepo.from_yaml(dep_stella_yaml_path)
            elif 'stella-yaml' in dep_dict:
                print('\t 📝 {} has an inline description in {}'.format(dep_dict['name'], root_path / 'stella.yaml'))
                dep = StellaRepo(dep_dict['stella-yaml'], dep_path, dep_dict['name'])
            else:
                print('\t 💣 {} has no stella.yaml file and no inline description!'.format(dep_dict['name']))
                sys.exit(1)

            if dep.project_name in resolved_dependency_names:
                print('\t ✅ Already covered {}'.format(dep.project_name))
                continue

            self.private_header_paths += dep.private_header_paths
            self.private_header_paths += dep.public_header_paths
            self.sources += dep.sources

            for sub_dependency in dep.dependencies:
                if sub_dependency['name'] not in resolved_dependency_names and sub_dependency not in unresolved_dependencies:
                    print('\t 🧩 Discovered {}\'s dependency {}'.format(dep.project_name, sub_dependency['name']))
                    unresolved_dependencies.append(sub_dependency)
                    self.dependencies.append(sub_dependency)
                else:
                    print('\t ✅ Already covered {}\'s dependency {}'.format(dep.project_name, sub_dependency['name']))

            print('\t 🏁 {} acquired'.format(dep.project_name))
            resolved_dependency_names.append(dep.project_name)

        print()

    def check_dependencies(self):

        print(' 🔍 Checking dependencies for local changes')
        for dep_dict in self.dependencies:
            dep_path = deps_path / dep_dict['name']
            git_repo = git.Repo.init(str(dep_path))
            if git_repo.is_dirty():
                print('\t 🚨 Dependency {} has local changes!'.format(dep_dict['name']))
            else:
                print('\t 🧼 Dependency {} is clean'.format(dep_dict['name']))        

    def generate_ninja_file(self, config, env):

        build_environment = get_build_environment(env)
        if not build_environment:
            print('platform "{}" not known to this script, and no override build environment provided')
            sys.exit(1)

        compiler_flags = '-std=c++20 -Wall -Wextra -Wno-unused-parameter -Werror -pedantic '
        if config == 'release':
            compiler_flags += '-O3 -flto '
        elif config == 'debug':
            compiler_flags += '-g '
        compiler_flags += build_environment['cxxflags']

        include_flags = ['-I{}'.format(x) for x in self.public_header_paths + self.private_header_paths]
        test_include_flags =  ['-I{}'.format(x) for x in self.test_header_paths]
        gtest_include_flags = ['-I{}'.format(gtest_inc_path)]

        static_lib_name = str(lib_path / 'lib{}.a'.format(self.project_name))
        shared_lib_name = str(lib_path / 'lib{}.so'.format(self.project_name))

        ninja = ninja_syntax.Writer(open(root_path / 'build.ninja', 'w'), width=120)
        ninja.comment('Generated file - do not edit!')
        ninja.newline()

        ninja.variable('cxx', build_environment['compiler'])
        ninja.variable('ld', build_environment['linker'])
        ninja.variable('ar', build_environment['archiver'])
        ninja.variable('cp', build_environment['copier'])
        ninja.variable('cxxflags', compiler_flags)
        ninja.variable('incflags', include_flags)
        ninja.variable('arflags', '-rcs')
        ninja.newline()

        ninja.rule('compile_exe', '$cxx -MD -MF $out.d $cxxflags $incflags $in -o $out', depfile='$out.d')
        ninja.rule('compile_static', '$cxx -MD -MF $out.d $cxxflags $incflags -c $in -o $out', depfile='$out.d')
        ninja.rule('compile_fpic', '$cxx -MD -MF $out.d $cxxflags $incflags -c -fPIC $in -o $out', depfile='$out.d')
        ninja.rule('link_static', '$ar $arflags $out $in', depfile='$out.d')
        ninja.rule('link_shared', '$cxx -MD -MF $out.d $cxxflags $incflags -shared -o $out $in', depfile='$out.d')
        ninja.rule('copy_file', '$cp $in $out')
        ninja.newline()

        for source in self.sources:
            ninja.build(source.object_name, 'compile_static', source.source_name)
            ninja.build(source.fpic_object_name, 'compile_fpic', source.source_name)
        ninja.newline()

        ninja.build(static_lib_name, 'link_static', [x.object_name for x in self.sources])
        ninja.newline()
        if self.build_static_lib:
            ninja.default(static_lib_name)

        ninja.build(shared_lib_name, 'link_shared', [x.fpic_object_name for x in self.sources])
        ninja.newline()
        if self.build_shared_lib:
            ninja.default(shared_lib_name)

        for source in self.apps:
            ninja.build(source.object_name, 'compile_static', source.source_name)
            ninja.build(source.executable_name, 'compile_exe', [x.object_name for x in self.sources]+[source.object_name])
            ninja.default(source.executable_name)
        ninja.newline()

        for public_header_path in self.public_header_paths:
            for header_file_path in public_header_path.glob('**/*'):
                header_file_src = str(header_file_path)
                if len(self.public_header_paths) == 1:
                    header_file_dst = str(inc_path / header_file_path.relative_to(public_header_path))
                else:
                    header_file_dst = str(inc_path / header_file_path.relative_to(self.project_path))
                ninja.build(header_file_dst, 'copy_file', header_file_src)
                if self.build_static_lib or self.build_shared_lib:
                    ninja.default(header_file_dst)
        ninja.newline()

        ninja.variable('testincflags', include_flags + test_include_flags + gtest_include_flags)
        ninja.variable('testlinkflags', '-L{}'.format(str(gtest_lib_path)))
        ninja.rule('compile_static_test', '$cxx -MD -MF $out.d $cxxflags $testincflags -c $in -o $out', depfile='$out.d')
        ninja.rule('compile_test_exe', '$cxx -MD -MF $out.d $cxxflags $testincflags $testlinkflags -lgtest $in -o $out', depfile='$out.d')
        ninja.newline()

        for source in self.tests:
            ninja.build(source.object_name, 'compile_static_test', source.source_name)

        test_src_path = test_path / 'src'
        test_compile_inputs = [x.object_name for x in self.tests] + \
                            [x.object_name for x in self.sources]
        ninja.build(test_target, 'compile_test_exe', test_compile_inputs)
        if len(self.tests):
            ninja.default(test_target)
        ninja.newline()

        ninja.close()


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Generate ninja.build')
    parser.add_argument('--config', metavar='CONFIG', type=str, help='The build configuration, either "release" or "debug"')
    parser.add_argument('--env', metavar='ENV_YAML', type=str, help='Override the platform-default build environment YAML blob')
    args = parser.parse_args()
    if not args.config:
        args.config = 'release'
    if args.config not in ['release', 'debug']:
        print('Config must be either "release" or "debug"')
        sys.exit(1)

    if pathlib.Path('.').absolute() != root_path.absolute():
        print('Error: configure.py must be run from the root of the project directory')
        exit(1)

    stella_repo = StellaRepo.from_yaml(root_path / 'stella.yaml')
    stella_repo.resolve_dependencies()
    stella_repo.generate_ninja_file(args.config, args.env)
    stella_repo.check_dependencies()
