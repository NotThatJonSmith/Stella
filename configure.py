#!/usr/bin/env python3

import sys
import os
import argparse

import yaml
import pathlib

sourcedir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(sourcedir, 'ninja', 'misc'))
import ninja_syntax

# TODO bootstrap ninja if we need to
# TODO add a monolithic build (all sources in one compile command)
# TODO add a 1TU build (generated source file that includes all others)
# TODO platform-specific flags and suffixes for making shared objects

class BuildEnvironment(object):

    def __init__(self, compiler, linker, archiver, copier):
        self.compiler = compiler
        self.linker = linker
        self.archiver = archiver
        self.copier = copier
    
    @classmethod
    def from_yaml(cls, yaml_path):

        env_file = yaml_path.open()
        env_dict = yaml.load(env_file, Loader=yaml.Loader)
        env_file.close()

        compiler = env_dict["compiler"]
        linker = env_dict["linker"]
        archiver = env_dict["archiver"]
        copier = env_dict["copier"]

        return cls(compiler, linker, archiver, copier)


class Clump(object):

    def __init__(self, project_name, project_path, public_header_paths,
                 private_header_paths, sources, dependencies, products):
        self.project_name = project_name
        self.project_path = project_path
        self.public_header_paths = public_header_paths
        self.private_header_paths = private_header_paths
        self.sources = sources
        self.dependencies = dependencies
        self.includes = [project_name]
        self.products = products

    @classmethod
    def from_yaml(cls, yaml_path):

        clump_file = yaml_path.open()
        clump_dict = yaml.load(clump_file, Loader=yaml.Loader)
        clump_file.close()

        project_path = yaml_path.parents[0]
        project_name = clump_dict['name']

        public_header_paths = []
        for string_path in clump_dict['public-header-paths']:
            building_path = project_path
            for path_piece in string_path.split('/'):
                building_path = building_path / path_piece
            public_header_paths.append(building_path)
        
        private_header_paths = []
        for string_path in clump_dict['private-header-paths']:
            building_path = project_path
            for path_piece in string_path.split('/'):
                building_path = building_path / path_piece
            private_header_paths.append(building_path)

        sources = []
        for source_glob in clump_dict['source-globs']:
            for source_path in project_path.glob(source_glob):
                if not source_path.is_dir():
                    sources.append(source_path)

        dependencies = clump_dict['dependencies']

        products = clump_dict['products']

        return cls(project_name, project_path, public_header_paths,
                   private_header_paths, sources, dependencies, products)

    def resolve_dependencies_within(self, root_path):

        all_clumps = {}
        for clump_file in list(root_path.glob('**/clump.yaml')):
            print("building a clump from {}".format(clump_file))
            m = Clump.from_yaml(clump_file)
            if (m.project_name in all_clumps.keys()):
                continue # TODO this is a warning condition
            all_clumps[m.project_name] = m

        while len(self.dependencies):
            
            dependency = all_clumps[self.dependencies[0]]
            self.private_header_paths += dependency.private_header_paths
            self.private_header_paths += dependency.public_header_paths
            self.sources += dependency.sources
            self.dependencies = [x for x in self.dependencies + dependency.dependencies if x not in self.includes and x not in dependency.includes]
            self.includes += dependency.includes # TODO uniquify

    def emit_ninja(self, output, build_environment, build_path=None, products=["app"], config="release"):

        if not build_path:
            build_path = pathlib.Path('.') / 'build'

        bin_path = build_path / 'bin'
        lib_path = build_path / 'lib'
        headers_path = build_path / 'include'
        obj_path = build_path / 'obj'

        ninja = ninja_syntax.Writer(output)

        ninja.comment('Generated file - do not edit!')
        ninja.newline()

        ninja.comment('Required tool locations on the build platform')
        ninja.variable('cxx', build_environment.compiler)
        ninja.variable('ld', build_environment.linker)
        ninja.variable('ar', build_environment.archiver)
        ninja.variable('cp', build_environment.copier)
        ninja.newline()

        compiler_flags = '-std=c++17 -Wall -Wextra -Wno-unused-parameter -Werror -pedantic'
        include_paths = " ".join(["-I{}".format(x) for x in self.public_header_paths + self.private_header_paths])
        if config == "release":
            compiler_flags += " -O3 -flto "
        elif config == "debug":
            compiler_flags += " -g "
        compiler_flags += include_paths

        ninja.comment("Compiler flags")
        ninja.variable("cxxflags", compiler_flags)
        ninja.variable("arflags", "-rcs")
        ninja.newline()

        ninja.comment("Build rule definitions")
        ninja.rule('compile_exe', '$cxx -MD -MF $out.d $cxxflags $in -o $out', depfile='$out.d')
        ninja.rule('compile_static', '$cxx -MD -MF $out.d $cxxflags -c $in -o $out', depfile='$out.d')
        ninja.rule('compile_fpic', '$cxx -MD -MF $out.d $cxxflags -c -fPIC $in -o $out', depfile='$out.d')
        ninja.rule('link_static', '$ar $arflags $out $in', depfile='$out.d')
        ninja.rule('link_shared', '$cxx -MD -MF $out.d $cxxflags -shared -o $out $in', depfile='$out.d')
        ninja.rule('copy_file', '$cp $in $out')
        ninja.newline()
        
        ninja.comment("Build fPIC and non-fPIC objects from all the sources")
        obj_names = []
        fpic_obj_names = []
        for this_src_path in self.sources:
            this_obj_path = obj_path / this_src_path
            this_obj_name = str(this_obj_path)+'.o'
            obj_names.append(this_obj_name)
            ninja.build(this_obj_name, 'compile_static', str(this_src_path))
            this_fpic_obj_name = str(this_obj_path)+'.fPIC.o'
            fpic_obj_names.append(this_fpic_obj_name)
            ninja.build(this_fpic_obj_name, 'compile_fpic', str(this_src_path))
        ninja.newline()

        static_lib_name = str(lib_path / "lib{}.a".format(self.project_name))
        ninja.comment("Build a static library from all the non-fPIC objects")
        ninja.build(static_lib_name, 'link_static', obj_names)
        ninja.newline()

        executable_name = str(bin_path / self.project_name)
        ninja.comment("Build an executable from all the non-fPIC objects")
        ninja.build(executable_name, 'compile_exe', obj_names)
        ninja.newline()

        shared_lib_name = str(lib_path / "lib{}.so".format(self.project_name))
        ninja.comment("Build a shared library from all the fPIC objects")
        ninja.build(shared_lib_name, 'link_shared', fpic_obj_names)
        ninja.newline()

        ninja.comment("Copy the public header files into the build products")
        for in_tree_header_path in self.public_header_paths:
            for in_tree_header_file in in_tree_header_path.glob('**/*'):
                in_build_header_file = headers_path / in_tree_header_file.relative_to(self.project_path)
                ninja.build(in_build_header_file, 'copy_file', in_tree_header_file)
        ninja.newline()

        if "app" in self.products:
            ninja.default(executable_name)
        elif "staticlib" in self.products:
            ninja.default(static_lib_name)
        elif "sharedlib" in self.products:
            ninja.default(shared_lib_name)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Generate ninja.build')
    parser.add_argument('target', metavar='TARGET', type=str,
                        help='The final build target')
    parser.add_argument('config', metavar='CONFIG', type=str,
                        help='The build configuration, either "release" or "debug"')
    args = parser.parse_args()

    if args.config not in ['release', 'debug']:
        print("Config must be either release or debug")

    root_path = pathlib.Path('.')
    build_environment = BuildEnvironment.from_yaml(root_path / 'build-environment.yaml')
    clump = Clump.from_yaml(root_path / 'clump.yaml')
    clump.resolve_dependencies_within(root_path)
    with open(root_path / 'build.ninja', 'w') as ninja_file:
        clump.emit_ninja(ninja_file, build_environment, config=args.config)
