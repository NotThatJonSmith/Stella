# Stella

Stella is a ninja build script generator, and opinionated way of structing and describing a constellation of projects in C/C++ such that:
* Final targets can build everything from source (and therefore enable whole-program optimization, with 1TU to come)
* It's easy to define any number of executable binary targets
* It's easy to build the whole of a repository with all of its dependencies as a static or shared library
* Every repository in the constellation can be cloned and worked on as the root of the project, and expect build products to make sense
* It's easy to configure release and debug builds that apply to the entire constellation

## TODO

* Resolve potential name collisions in cases where two dependencies of a project might internally use the same name for a header
* Set up clean and refresh option that's cleaner than nuking the deps folder
