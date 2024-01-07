#!/usr/bin/env python

import json
import re
import shutil
from pathlib import Path


def generate_mod_version(argcount, const=False, returns=False):
    s = """
#define MODBIND$VER($RETTYPE m_name$ARG) \\
virtual $RETVAL _##m_name($FUNCARGS) $CONST override; \\
"""
    sproto = str(argcount)
    if returns:
        sproto += "R"
        s = s.replace("$RETTYPE", "m_ret, ")
        s = s.replace("$RETVAL", "m_ret")

    else:
        s = s.replace("$RETTYPE", "")
        s = s.replace("$RETVAL", "void")

    if const:
        sproto += "C"
        s = s.replace("$CONST", "const")
    else:
        s = s.replace("$CONST", "")

    s = s.replace("$VER", sproto)
    argtext = ""
    funcargs = ""

    for i in range(argcount):
        if i > 0:
            funcargs += ", "

        argtext += ", m_type" + str(i + 1)
        funcargs += "m_type" + str(i + 1) + " arg" + str(i + 1)

    if argcount:
        s = s.replace("$ARG", argtext)
        s = s.replace("$FUNCARGS", funcargs)
    else:
        s = s.replace("$ARG", "")
        s = s.replace("$FUNCARGS", funcargs)

    return s


def generate_wrappers(target):
    max_versions = 12

    txt = """
#ifndef GDEXTENSION_WRAPPERS_GEN_H
#define GDEXTENSION_WRAPPERS_GEN_H

"""

    for i in range(max_versions + 1):
        txt += "\n/* Module Wrapper " + str(i) + " Arguments */\n"
        txt += generate_mod_version(i, False, False)
        txt += generate_mod_version(i, False, True)
        txt += generate_mod_version(i, True, False)
        txt += generate_mod_version(i, True, True)

    txt += "\n#endif\n"

    with open(target, "w", encoding="utf-8") as f:
        f.write(txt)


def get_file_list(api_filepath, output_dir, headers=False, sources=False):
    api = {}
    files = []
    with open(api_filepath, encoding="utf-8") as api_file:
        api = json.load(api_file)

    core_gen_folder = Path(output_dir) / "gen" / "include" / "godot_cpp" / "core"
    include_gen_folder = Path(output_dir) / "gen" / "include" / "godot_cpp"
    source_gen_folder = Path(output_dir) / "gen" / "src"

    files.append(str((core_gen_folder / "ext_wrappers.gen.inc").as_posix()))

    for builtin_class in api["builtin_classes"]:
        if is_pod_type(builtin_class["name"]):
            continue

        if is_included_type(builtin_class["name"]):
            continue

        header_filename = include_gen_folder / "variant" / (camel_to_snake(builtin_class["name"]) + ".hpp")
        source_filename = source_gen_folder / "variant" / (camel_to_snake(builtin_class["name"]) + ".cpp")
        if headers:
            files.append(str(header_filename.as_posix()))
        if sources:
            files.append(str(source_filename.as_posix()))

    for engine_class in api["classes"]:
        # Generate code for the ClassDB singleton under a different name.
        if engine_class["name"] == "ClassDB":
            engine_class["name"] = "ClassDBSingleton"
            engine_class["alias_for"] = "ClassDB"
        header_filename = include_gen_folder / "classes" / (camel_to_snake(engine_class["name"]) + ".hpp")
        source_filename = source_gen_folder / "classes" / (camel_to_snake(engine_class["name"]) + ".cpp")
        if headers:
            files.append(str(header_filename.as_posix()))
        if sources:
            files.append(str(source_filename.as_posix()))

    for native_struct in api["native_structures"]:
        struct_name = native_struct["name"]
        snake_struct_name = camel_to_snake(struct_name)

        header_filename = include_gen_folder / "classes" / (snake_struct_name + ".hpp")
        if headers:
            files.append(str(header_filename.as_posix()))

    if headers:
        for path in [
            include_gen_folder / "variant" / "builtin_types.hpp",
            include_gen_folder / "variant" / "builtin_binds.hpp",
            include_gen_folder / "variant" / "utility_functions.hpp",
            include_gen_folder / "variant" / "variant_size.hpp",
            include_gen_folder / "classes" / "global_constants.hpp",
            include_gen_folder / "classes" / "global_constants_binds.hpp",
            include_gen_folder / "core" / "version.hpp",
        ]:
            files.append(str(path.as_posix()))
    if sources:
        utility_functions_source_path = source_gen_folder / "variant" / "utility_functions.cpp"
        files.append(str(utility_functions_source_path.as_posix()))

    return files


def print_file_list(api_filepath, output_dir, headers=False, sources=False):
    print(*get_file_list(api_filepath, output_dir, headers, sources), sep=";", end=None)


def scons_emit_files(target, source, env):
    files = [env.File(f) for f in get_file_list(str(source[0]), target[0].abspath, True, True)]
    env.Clean(target, files)
    env["godot_cpp_gen_dir"] = target[0].abspath
    return files, source


def scons_generate_bindings(target, source, env):
    generate_bindings(
        str(source[0]),
        env["generate_template_get_node"],
        "32" if "32" in env["arch"] else "64",
        env["precision"],
        env["godot_cpp_gen_dir"],
    )
    return None


def generate_bindings(api_filepath, use_template_get_node, bits="64", precision="single", output_dir="."):
    api = None

    target_dir = Path(output_dir) / "gen"

    with open(api_filepath, encoding="utf-8") as api_file:
        api = json.load(api_file)

    shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True)

    real_t = "double" if precision == "double" else "float"
    print("Built-in type config: " + real_t + "_" + bits)

    generate_global_constants(api, target_dir)
    generate_version_header(api, target_dir)
    generate_global_constant_binds(api, target_dir)
    generate_builtin_bindings(api, target_dir, real_t + "_" + bits)
    generate_engine_classes_bindings(api, target_dir, use_template_get_node)
    generate_utility_functions(api, target_dir)


builtin_classes = []

# Key is class name, value is boolean where True means the class is refcounted.
engine_classes = {}

# Type names of native structures
native_structures = []

singletons = []


def generate_builtin_bindings(api, output_dir, build_config):
    global builtin_classes

    core_gen_folder = Path(output_dir) / "include" / "godot_cpp" / "core"
    include_gen_folder = Path(output_dir) / "include" / "godot_cpp" / "variant"
    source_gen_folder = Path(output_dir) / "src" / "variant"

    core_gen_folder.mkdir(parents=True, exist_ok=True)
    include_gen_folder.mkdir(parents=True, exist_ok=True)
    source_gen_folder.mkdir(parents=True, exist_ok=True)

    generate_wrappers(core_gen_folder / "ext_wrappers.gen.inc")

    # Store types beforehand.
    for builtin_api in api["builtin_classes"]:
        if is_pod_type(builtin_api["name"]):
            continue
        builtin_classes.append(builtin_api["name"])

    builtin_sizes = {}

    for size_list in api["builtin_class_sizes"]:
        if size_list["build_configuration"] == build_config:
            for size in size_list["sizes"]:
                builtin_sizes[size["name"]] = size["size"]
            break

    # Create a file for Variant size, since that class isn't generated.
    variant_size_filename = include_gen_folder / "variant_size.hpp"
    with variant_size_filename.open("+w", encoding="utf-8") as variant_size_file:
        variant_size_source = []
        add_header("variant_size.hpp", variant_size_source)

        header_guard = "GODOT_CPP_VARIANT_SIZE_HPP"
        variant_size_source.append(f"#ifndef {header_guard}")
        variant_size_source.append(f"#define {header_guard}")
        variant_size_source.append(f'#define GODOT_CPP_VARIANT_SIZE {builtin_sizes["Variant"]}')
        variant_size_source.append(f"#endif // ! {header_guard}")

        variant_size_file.write("\n".join(variant_size_source))

    for builtin_api in api["builtin_classes"]:
        if is_pod_type(builtin_api["name"]):
            continue
        if is_included_type(builtin_api["name"]):
            continue

        size = builtin_sizes[builtin_api["name"]]

        header_filename = include_gen_folder / (camel_to_snake(builtin_api["name"]) + ".hpp")
        source_filename = source_gen_folder / (camel_to_snake(builtin_api["name"]) + ".cpp")

        # Check used classes for header include
        used_classes = set()
        fully_used_classes = set()

        class_name = builtin_api["name"]

        if "constructors" in builtin_api:
            for constructor in builtin_api["constructors"]:
                if "arguments" in constructor:
                    for argument in constructor["arguments"]:
                        if is_included(argument["type"], class_name):
                            if "default_value" in argument and argument["type"] != "Variant":
                                fully_used_classes.add(argument["type"])
                            else:
                                used_classes.add(argument["type"])

        if "methods" in builtin_api:
            for method in builtin_api["methods"]:
                if "arguments" in method:
                    for argument in method["arguments"]:
                        if is_included(argument["type"], class_name):
                            if "default_value" in argument and argument["type"] != "Variant":
                                fully_used_classes.add(argument["type"])
                            else:
                                used_classes.add(argument["type"])
                if "return_type" in method:
                    if is_included(method["return_type"], class_name):
                        used_classes.add(method["return_type"])

        if "members" in builtin_api:
            for member in builtin_api["members"]:
                if is_included(member["type"], class_name):
                    used_classes.add(member["type"])

        if "indexing_return_type" in builtin_api:
            if is_included(builtin_api["indexing_return_type"], class_name):
                used_classes.add(builtin_api["indexing_return_type"])

        if "operators" in builtin_api:
            for operator in builtin_api["operators"]:
                if "right_type" in operator:
                    if is_included(operator["right_type"], class_name):
                        used_classes.add(operator["right_type"])

        for type_name in fully_used_classes:
            if type_name in used_classes:
                used_classes.remove(type_name)

        used_classes = list(used_classes)
        used_classes.sort()
        fully_used_classes = list(fully_used_classes)
        fully_used_classes.sort()

        with header_filename.open("w+", encoding="utf-8") as header_file:
            header_file.write(generate_builtin_class_header(builtin_api, size, used_classes, fully_used_classes))

        with source_filename.open("w+", encoding="utf-8") as source_file:
            source_file.write(generate_builtin_class_source(builtin_api, size, used_classes, fully_used_classes))

    # Create a header with all builtin types for convenience.
    builtin_header_filename = include_gen_folder / "builtin_types.hpp"
    with builtin_header_filename.open("w+", encoding="utf-8") as builtin_header_file:
        builtin_header = []
        add_header("builtin_types.hpp", builtin_header)

        builtin_header.append("#ifndef GODOT_CPP_BUILTIN_TYPES_HPP")
        builtin_header.append("#define GODOT_CPP_BUILTIN_TYPES_HPP")

        builtin_header.append("")

        for builtin in builtin_classes:
            builtin_header.append(f"#include <godot_cpp/variant/{camel_to_snake(builtin)}.hpp>")

        builtin_header.append("")

        builtin_header.append("#endif // ! GODOT_CPP_BUILTIN_TYPES_HPP")

        builtin_header_file.write("\n".join(builtin_header))

    # Create a header with bindings for builtin types.
    builtin_binds_filename = include_gen_folder / "builtin_binds.hpp"
    with builtin_binds_filename.open("w+", encoding="utf-8") as builtin_binds_file:
        builtin_binds = []
        add_header("builtin_binds.hpp", builtin_binds)

        builtin_binds.append("#ifndef GODOT_CPP_BUILTIN_BINDS_HPP")
        builtin_binds.append("#define GODOT_CPP_BUILTIN_BINDS_HPP")
        builtin_binds.append("")
        builtin_binds.append("#include <godot_cpp/variant/builtin_types.hpp>")
        builtin_binds.append("")

        for builtin_api in api["builtin_classes"]:
            if is_included_type(builtin_api["name"]):
                if "enums" in builtin_api:
                    for enum_api in builtin_api["enums"]:
                        builtin_binds.append(f"VARIANT_ENUM_CAST({builtin_api['name']}::{enum_api['name']});")

        builtin_binds.append("")
        builtin_binds.append("#endif // ! GODOT_CPP_BUILTIN_BINDS_HPP")

        builtin_binds_file.write("\n".join(builtin_binds))


def generate_builtin_class_header(builtin_api, size, used_classes, fully_used_classes):
    result = []

    class_name = builtin_api["name"]
    snake_class_name = camel_to_snake(class_name).upper()

    header_guard = f"GODOT_CPP_{snake_class_name}_HPP"

    add_header(f"{snake_class_name.lower()}.hpp", result)

    result.append(f"#ifndef {header_guard}")
    result.append(f"#define {header_guard}")

    result.append("")
    result.append("#include <godot_cpp/core/defs.hpp>")
    result.append("")

    # Special cases.
    if class_name == "String":
        result.append("#include <godot_cpp/variant/char_string.hpp>")
        result.append("#include <godot_cpp/variant/char_utils.hpp>")

    if class_name == "PackedStringArray":
        result.append("#include <godot_cpp/variant/string.hpp>")
    if class_name == "PackedColorArray":
        result.append("#include <godot_cpp/variant/color.hpp>")
    if class_name == "PackedVector2Array":
        result.append("#include <godot_cpp/variant/vector2.hpp>")
    if class_name == "PackedVector3Array":
        result.append("#include <godot_cpp/variant/vector3.hpp>")

    if class_name == "Array":
        result.append("#include <godot_cpp/variant/array_helpers.hpp>")

    for include in fully_used_classes:
        if include == "TypedArray":
            result.append("#include <godot_cpp/variant/typed_array.hpp>")
        else:
            result.append(f"#include <godot_cpp/{get_include_path(include)}>")

    if len(fully_used_classes) > 0:
        result.append("")

    result.append("#include <gdextension_interface.h>")
    result.append("")
    result.append("namespace godot {")
    result.append("")

    for type_name in used_classes:
        if is_struct_type(type_name):
            result.append(f"struct {type_name};")
        else:
            result.append(f"class {type_name};")

    if len(used_classes) > 0:
        result.append("")

    result.append(f"class {class_name} {{")
    result.append(f"\tstatic constexpr size_t {snake_class_name}_SIZE = {size};")
    result.append(f"\tuint8_t opaque[{snake_class_name}_SIZE] = {{}};")

    result.append("")
    result.append("\tfriend class Variant;")
    if class_name == "String":
        result.append("\tfriend class StringName;")

    result.append("")
    result.append("\tstatic struct _MethodBindings {")

    if "constructors" in builtin_api:
        for constructor in builtin_api["constructors"]:
            result.append(f'\t\tGDExtensionPtrConstructor constructor_{constructor["index"]};')

    if builtin_api["has_destructor"]:
        result.append("\t\tGDExtensionPtrDestructor destructor;")

    if "methods" in builtin_api:
        for method in builtin_api["methods"]:
            result.append(f'\t\tGDExtensionPtrBuiltInMethod method_{method["name"]};')

    if "members" in builtin_api:
        for member in builtin_api["members"]:
            result.append(f'\t\tGDExtensionPtrSetter member_{member["name"]}_setter;')
            result.append(f'\t\tGDExtensionPtrGetter member_{member["name"]}_getter;')

    if "indexing_return_type" in builtin_api:
        result.append("\t\tGDExtensionPtrIndexedSetter indexed_setter;")
        result.append("\t\tGDExtensionPtrIndexedGetter indexed_getter;")

    if "is_keyed" in builtin_api and builtin_api["is_keyed"]:
        result.append("\t\tGDExtensionPtrKeyedSetter keyed_setter;")
        result.append("\t\tGDExtensionPtrKeyedGetter keyed_getter;")
        result.append("\t\tGDExtensionPtrKeyedChecker keyed_checker;")

    if "operators" in builtin_api:
        for operator in builtin_api["operators"]:
            if "right_type" in operator:
                result.append(
                    f'\t\tGDExtensionPtrOperatorEvaluator operator_{get_operator_id_name(operator["name"])}_{operator["right_type"]};'
                )
            else:
                result.append(f'\t\tGDExtensionPtrOperatorEvaluator operator_{get_operator_id_name(operator["name"])};')

    result.append("\t} _method_bindings;")

    result.append("")
    result.append("\tstatic void init_bindings();")
    result.append("\tstatic void _init_bindings_constructors_destructor();")

    result.append("")
    result.append("public:")

    result.append(
        f"\t_FORCE_INLINE_ GDExtensionTypePtr _native_ptr() const {{ return const_cast<uint8_t (*)[{snake_class_name}_SIZE]>(&opaque); }}"
    )

    copy_constructor_index = -1

    if "constructors" in builtin_api:
        for constructor in builtin_api["constructors"]:
            method_signature = f"\t{class_name}("
            if "arguments" in constructor:
                method_signature += make_function_parameters(
                    constructor["arguments"], include_default=True, for_builtin=True
                )
                if len(constructor["arguments"]) == 1 and constructor["arguments"][0]["type"] == class_name:
                    copy_constructor_index = constructor["index"]
            method_signature += ");"

            result.append(method_signature)

    # Move constructor.
    result.append(f"\t{class_name}({class_name} &&other);")

    # Special cases.
    if class_name == "String" or class_name == "StringName" or class_name == "NodePath":
        result.append(f"\t{class_name}(const char *from);")
        result.append(f"\t{class_name}(const wchar_t *from);")
        result.append(f"\t{class_name}(const char16_t *from);")
        result.append(f"\t{class_name}(const char32_t *from);")

    if "constants" in builtin_api:
        axis_constants_count = 0
        for constant in builtin_api["constants"]:
            # Special case: Vector3.Axis is the only enum in the bindings.
            # It's technically not supported by Variant but works for direct access.
            if class_name == "Vector3" and constant["name"].startswith("AXIS"):
                if axis_constants_count == 0:
                    result.append("\tenum Axis {")
                result.append(f'\t\t{constant["name"]} = {constant["value"]},')
                axis_constants_count += 1
                if axis_constants_count == 3:
                    result.append("\t};")
            else:
                result.append(f'\tstatic const {correct_type(constant["type"])} {constant["name"]};')

    if builtin_api["has_destructor"]:
        result.append(f"\t~{class_name}();")

    method_list = []

    if "methods" in builtin_api:
        for method in builtin_api["methods"]:
            method_list.append(method["name"])

            vararg = method["is_vararg"]
            if vararg:
                result.append("\ttemplate<class... Args>")

            method_signature = "\t"
            if "is_static" in method and method["is_static"]:
                method_signature += "static "

            if "return_type" in method:
                method_signature += f'{correct_type(method["return_type"])} '
            else:
                method_signature += "void "

            method_signature += f'{method["name"]}('

            method_arguments = []
            if "arguments" in method:
                method_arguments = method["arguments"]

            method_signature += make_function_parameters(
                method_arguments, include_default=True, for_builtin=True, is_vararg=vararg
            )

            method_signature += ")"
            if method["is_const"]:
                method_signature += " const"
            method_signature += ";"

            result.append(method_signature)

    # Special cases.
    if class_name == "String":
        result.append("\tstatic String utf8(const char *from, int len = -1);")
        result.append("\tvoid parse_utf8(const char *from, int len = -1);")
        result.append("\tstatic String utf16(const char16_t *from, int len = -1);")
        result.append("\tvoid parse_utf16(const char16_t *from, int len = -1);")
        result.append("\tCharString utf8() const;")
        result.append("\tCharString ascii() const;")
        result.append("\tChar16String utf16() const;")
        result.append("\tChar32String utf32() const;")
        result.append("\tCharWideString wide_string() const;")
        result.append("\tstatic String num_real(double p_num, bool p_trailing = true);")

    if "members" in builtin_api:
        for member in builtin_api["members"]:
            if f'get_{member["name"]}' not in method_list:
                result.append(f'\t{correct_type(member["type"])} get_{member["name"]}() const;')
            if f'set_{member["name"]}' not in method_list:
                result.append(f'\tvoid set_{member["name"]}({type_for_parameter(member["type"])}value);')

    if "operators" in builtin_api:
        for operator in builtin_api["operators"]:
            if operator["name"] not in ["in", "xor"]:
                if "right_type" in operator:
                    result.append(
                        f'\t{correct_type(operator["return_type"])} operator{operator["name"]}({type_for_parameter(operator["right_type"])}other) const;'
                    )
                else:
                    result.append(
                        f'\t{correct_type(operator["return_type"])} operator{operator["name"].replace("unary", "")}() const;'
                    )

    # Copy assignment.
    if copy_constructor_index >= 0:
        result.append(f"\t{class_name} &operator=(const {class_name} &other);")

    # Move assignment.
    result.append(f"\t{class_name} &operator=({class_name} &&other);")

    # Special cases.
    if class_name == "String":
        result.append("\tString &operator=(const char *p_str);")
        result.append("\tString &operator=(const wchar_t *p_str);")
        result.append("\tString &operator=(const char16_t *p_str);")
        result.append("\tString &operator=(const char32_t *p_str);")
        result.append("\tbool operator==(const char *p_str) const;")
        result.append("\tbool operator==(const wchar_t *p_str) const;")
        result.append("\tbool operator==(const char16_t *p_str) const;")
        result.append("\tbool operator==(const char32_t *p_str) const;")
        result.append("\tbool operator!=(const char *p_str) const;")
        result.append("\tbool operator!=(const wchar_t *p_str) const;")
        result.append("\tbool operator!=(const char16_t *p_str) const;")
        result.append("\tbool operator!=(const char32_t *p_str) const;")
        result.append("\tString operator+(const char *p_str);")
        result.append("\tString operator+(const wchar_t *p_str);")
        result.append("\tString operator+(const char16_t *p_str);")
        result.append("\tString operator+(const char32_t *p_str);")
        result.append("\tString operator+(char32_t p_char);")
        result.append("\tString &operator+=(const String &p_str);")
        result.append("\tString &operator+=(char32_t p_char);")
        result.append("\tString &operator+=(const char *p_str);")
        result.append("\tString &operator+=(const wchar_t *p_str);")
        result.append("\tString &operator+=(const char32_t *p_str);")

        result.append("\tconst char32_t &operator[](int p_index) const;")
        result.append("\tchar32_t &operator[](int p_index);")
        result.append("\tconst char32_t *ptr() const;")
        result.append("\tchar32_t *ptrw();")

    if class_name == "Array":
        result.append("\ttemplate <class... Args>")
        result.append("\tstatic Array make(Args... args) {")
        result.append("\t\treturn helpers::append_all(Array(), args...);")
        result.append("\t}")

    if is_packed_array(class_name):
        return_type = correct_type(builtin_api["indexing_return_type"])
        if class_name == "PackedByteArray":
            return_type = "uint8_t"
        elif class_name == "PackedInt32Array":
            return_type = "int32_t"
        elif class_name == "PackedFloat32Array":
            return_type = "float"
        result.append(f"\tconst {return_type} &operator[](int p_index) const;")
        result.append(f"\t{return_type} &operator[](int p_index);")
        result.append(f"\tconst {return_type} *ptr() const;")
        result.append(f"\t{return_type} *ptrw();")
        iterators = """
    struct Iterator {
		_FORCE_INLINE_ $TYPE &operator*() const {
			return *elem_ptr;
		}
		_FORCE_INLINE_ $TYPE *operator->() const { return elem_ptr; }
		_FORCE_INLINE_ Iterator &operator++() {
			elem_ptr++;
			return *this;
		}
		_FORCE_INLINE_ Iterator &operator--() {
			elem_ptr--;
			return *this;
		}

		_FORCE_INLINE_ bool operator==(const Iterator &b) const { return elem_ptr == b.elem_ptr; }
		_FORCE_INLINE_ bool operator!=(const Iterator &b) const { return elem_ptr != b.elem_ptr; }

		Iterator($TYPE *p_ptr) { elem_ptr = p_ptr; }
		Iterator() {}
		Iterator(const Iterator &p_it) { elem_ptr = p_it.elem_ptr; }

	private:
		$TYPE *elem_ptr = nullptr;
	};

	struct ConstIterator {
		_FORCE_INLINE_ const $TYPE &operator*() const {
			return *elem_ptr;
		}
		_FORCE_INLINE_ const $TYPE *operator->() const { return elem_ptr; }
		_FORCE_INLINE_ ConstIterator &operator++() {
			elem_ptr++;
			return *this;
		}
		_FORCE_INLINE_ ConstIterator &operator--() {
			elem_ptr--;
			return *this;
		}

		_FORCE_INLINE_ bool operator==(const ConstIterator &b) const { return elem_ptr == b.elem_ptr; }
		_FORCE_INLINE_ bool operator!=(const ConstIterator &b) const { return elem_ptr != b.elem_ptr; }

		ConstIterator(const $TYPE *p_ptr) { elem_ptr = p_ptr; }
		ConstIterator() {}
		ConstIterator(const ConstIterator &p_it) { elem_ptr = p_it.elem_ptr; }

	private:
		const $TYPE *elem_ptr = nullptr;
	};

	_FORCE_INLINE_ Iterator begin() {
		return Iterator(ptrw());
	}
	_FORCE_INLINE_ Iterator end() {
		return Iterator(ptrw() + size());
	}

	_FORCE_INLINE_ ConstIterator begin() const {
		return ConstIterator(ptr());
	}
	_FORCE_INLINE_ ConstIterator end() const {
		return ConstIterator(ptr() + size());
	}
"""
        result.append(iterators.replace("$TYPE", return_type))

    if class_name == "Array":
        result.append("\tconst Variant &operator[](int p_index) const;")
        result.append("\tVariant &operator[](int p_index);")
        result.append("\tvoid set_typed(uint32_t p_type, const StringName &p_class_name, const Variant &p_script);")
        result.append("\tvoid _ref(const Array &p_from) const;")

    if class_name == "Dictionary":
        result.append("\tconst Variant &operator[](const Variant &p_key) const;")
        result.append("\tVariant &operator[](const Variant &p_key);")

    result.append("};")

    if class_name == "String":
        result.append("")
        result.append("bool operator==(const char *p_chr, const String &p_str);")
        result.append("bool operator==(const wchar_t *p_chr, const String &p_str);")
        result.append("bool operator==(const char16_t *p_chr, const String &p_str);")
        result.append("bool operator==(const char32_t *p_chr, const String &p_str);")
        result.append("bool operator!=(const char *p_chr, const String &p_str);")
        result.append("bool operator!=(const wchar_t *p_chr, const String &p_str);")
        result.append("bool operator!=(const char16_t *p_chr, const String &p_str);")
        result.append("bool operator!=(const char32_t *p_chr, const String &p_str);")
        result.append("String operator+(const char *p_chr, const String &p_str);")
        result.append("String operator+(const wchar_t *p_chr, const String &p_str);")
        result.append("String operator+(const char16_t *p_chr, const String &p_str);")
        result.append("String operator+(const char32_t *p_chr, const String &p_str);")
        result.append("String operator+(char32_t p_char, const String &p_str);")

        result.append("String itos(int64_t p_val);")
        result.append("String uitos(uint64_t p_val);")
        result.append("String rtos(double p_val);")
        result.append("String rtoss(double p_val);")

    result.append("")
    result.append("} // namespace godot")

    result.append(f"#endif // ! {header_guard}")

    return "\n".join(result)


def generate_builtin_class_source(builtin_api, size, used_classes, fully_used_classes):
    result = []

    class_name = builtin_api["name"]
    snake_class_name = camel_to_snake(class_name)
    enum_type_name = f"GDEXTENSION_VARIANT_TYPE_{snake_class_name.upper()}"

    add_header(f"{snake_class_name}.cpp", result)

    result.append("")
    result.append(f"#include <godot_cpp/variant/{snake_class_name}.hpp>")
    result.append("")
    result.append("#include <godot_cpp/core/binder_common.hpp>")
    result.append("")
    result.append("#include <godot_cpp/godot.hpp>")
    result.append("")

    # Only used since the "fully used" is included in header already.
    for include in used_classes:
        result.append(f"#include <godot_cpp/{get_include_path(include)}>")

    if len(used_classes) > 0:
        result.append("")

    result.append("#include <godot_cpp/core/builtin_ptrcall.hpp>")
    result.append("")
    result.append("#include <utility>")
    result.append("")
    result.append("namespace godot {")
    result.append("")

    result.append(f"{class_name}::_MethodBindings {class_name}::_method_bindings;")
    result.append("")

    result.append(f"void {class_name}::_init_bindings_constructors_destructor() {{")

    if "constructors" in builtin_api:
        for constructor in builtin_api["constructors"]:
            result.append(
                f'\t_method_bindings.constructor_{constructor["index"]} = internal::gdextension_interface_variant_get_ptr_constructor({enum_type_name}, {constructor["index"]});'
            )

    if builtin_api["has_destructor"]:
        result.append(
            f"\t_method_bindings.destructor = internal::gdextension_interface_variant_get_ptr_destructor({enum_type_name});"
        )

    result.append("}")

    result.append(f"void {class_name}::init_bindings() {{")

    # StringName's constructor internally uses String, so it constructor must be ready !
    if class_name == "StringName":
        result.append("\tString::_init_bindings_constructors_destructor();")
    result.append(f"\t{class_name}::_init_bindings_constructors_destructor();")

    result.append("\tStringName _gde_name;")

    if "methods" in builtin_api:
        for method in builtin_api["methods"]:
            # TODO: Add error check for hash mismatch.
            result.append(f'\t_gde_name = StringName("{method["name"]}");')
            result.append(
                f'\t_method_bindings.method_{method["name"]} = internal::gdextension_interface_variant_get_ptr_builtin_method({enum_type_name}, _gde_name._native_ptr(), {method["hash"]});'
            )

    if "members" in builtin_api:
        for member in builtin_api["members"]:
            result.append(f'\t_gde_name = StringName("{member["name"]}");')
            result.append(
                f'\t_method_bindings.member_{member["name"]}_setter = internal::gdextension_interface_variant_get_ptr_setter({enum_type_name}, _gde_name._native_ptr());'
            )
            result.append(
                f'\t_method_bindings.member_{member["name"]}_getter = internal::gdextension_interface_variant_get_ptr_getter({enum_type_name}, _gde_name._native_ptr());'
            )

    if "indexing_return_type" in builtin_api:
        result.append(
            f"\t_method_bindings.indexed_setter = internal::gdextension_interface_variant_get_ptr_indexed_setter({enum_type_name});"
        )
        result.append(
            f"\t_method_bindings.indexed_getter = internal::gdextension_interface_variant_get_ptr_indexed_getter({enum_type_name});"
        )

    if "is_keyed" in builtin_api and builtin_api["is_keyed"]:
        result.append(
            f"\t_method_bindings.keyed_setter = internal::gdextension_interface_variant_get_ptr_keyed_setter({enum_type_name});"
        )
        result.append(
            f"\t_method_bindings.keyed_getter = internal::gdextension_interface_variant_get_ptr_keyed_getter({enum_type_name});"
        )
        result.append(
            f"\t_method_bindings.keyed_checker = internal::gdextension_interface_variant_get_ptr_keyed_checker({enum_type_name});"
        )

    if "operators" in builtin_api:
        for operator in builtin_api["operators"]:
            if "right_type" in operator:
                if operator["right_type"] == "Variant":
                    right_type_variant_type = "GDEXTENSION_VARIANT_TYPE_NIL"
                else:
                    right_type_variant_type = (
                        f"GDEXTENSION_VARIANT_TYPE_{camel_to_snake(operator['right_type']).upper()}"
                    )
                result.append(
                    f'\t_method_bindings.operator_{get_operator_id_name(operator["name"])}_{operator["right_type"]} = internal::gdextension_interface_variant_get_ptr_operator_evaluator(GDEXTENSION_VARIANT_OP_{get_operator_id_name(operator["name"]).upper()}, {enum_type_name}, {right_type_variant_type});'
                )
            else:
                result.append(
                    f'\t_method_bindings.operator_{get_operator_id_name(operator["name"])} = internal::gdextension_interface_variant_get_ptr_operator_evaluator(GDEXTENSION_VARIANT_OP_{get_operator_id_name(operator["name"]).upper()}, {enum_type_name}, GDEXTENSION_VARIANT_TYPE_NIL);'
                )

    result.append("}")
    result.append("")

    copy_constructor_index = -1

    if "constructors" in builtin_api:
        for constructor in builtin_api["constructors"]:
            method_signature = f"{class_name}::{class_name}("
            if "arguments" in constructor:
                method_signature += make_function_parameters(
                    constructor["arguments"], include_default=False, for_builtin=True
                )
            method_signature += ") {"

            result.append(method_signature)

            method_call = (
                f'\tinternal::_call_builtin_constructor(_method_bindings.constructor_{constructor["index"]}, &opaque'
            )
            if "arguments" in constructor:
                if len(constructor["arguments"]) == 1 and constructor["arguments"][0]["type"] == class_name:
                    copy_constructor_index = constructor["index"]
                method_call += ", "
                arguments = []
                for argument in constructor["arguments"]:
                    (encode, arg_name) = get_encoded_arg(
                        argument["name"],
                        argument["type"],
                        argument["meta"] if "meta" in argument else None,
                    )
                    result += encode
                    arguments.append(arg_name)
                method_call += ", ".join(arguments)
            method_call += ");"

            result.append(method_call)
            result.append("}")
            result.append("")

    # Move constructor.
    result.append(f"{class_name}::{class_name}({class_name} &&other) {{")
    if needs_copy_instead_of_move(class_name) and copy_constructor_index >= 0:
        result.append(
            f"\tinternal::_call_builtin_constructor(_method_bindings.constructor_{copy_constructor_index}, &opaque, &other);"
        )
    else:
        result.append("\tstd::swap(opaque, other.opaque);")
    result.append("}")
    result.append("")

    if builtin_api["has_destructor"]:
        result.append(f"{class_name}::~{class_name}() {{")
        result.append("\t_method_bindings.destructor(&opaque);")
        result.append("}")
        result.append("")

    method_list = []

    if "methods" in builtin_api:
        for method in builtin_api["methods"]:
            method_list.append(method["name"])

            if "is_vararg" in method and method["is_vararg"]:
                # Done in the header because of the template.
                continue

            method_signature = make_signature(class_name, method, for_builtin=True)
            result.append(method_signature + "{")

            method_call = "\t"
            if "return_type" in method:
                method_call += f'return internal::_call_builtin_method_ptr_ret<{correct_type(method["return_type"])}>('
            else:
                method_call += "internal::_call_builtin_method_ptr_no_ret("
            method_call += f'_method_bindings.method_{method["name"]}, '
            if "is_static" in method and method["is_static"]:
                method_call += "nullptr"
            else:
                method_call += "(GDExtensionTypePtr)&opaque"

            if "arguments" in method:
                arguments = []
                method_call += ", "
                for argument in method["arguments"]:
                    (encode, arg_name) = get_encoded_arg(
                        argument["name"],
                        argument["type"],
                        argument["meta"] if "meta" in argument else None,
                    )
                    result += encode
                    arguments.append(arg_name)
                method_call += ", ".join(arguments)
            method_call += ");"

            result.append(method_call)
            result.append("}")
            result.append("")

    if "members" in builtin_api:
        for member in builtin_api["members"]:
            if f'get_{member["name"]}' not in method_list:
                result.append(f'{correct_type(member["type"])} {class_name}::get_{member["name"]}() const {{')
                result.append(
                    f'\treturn internal::_call_builtin_ptr_getter<{correct_type(member["type"])}>(_method_bindings.member_{member["name"]}_getter, (GDExtensionConstTypePtr)&opaque);'
                )
                result.append("}")

            if f'set_{member["name"]}' not in method_list:
                result.append(f'void {class_name}::set_{member["name"]}({type_for_parameter(member["type"])}value) {{')
                (encode, arg_name) = get_encoded_arg("value", member["type"], None)
                result += encode
                result.append(
                    f'\t_method_bindings.member_{member["name"]}_setter((GDExtensionConstTypePtr)&opaque, (GDExtensionConstTypePtr){arg_name});'
                )

                result.append("}")
            result.append("")

    if "operators" in builtin_api:
        for operator in builtin_api["operators"]:
            if operator["name"] not in ["in", "xor"]:
                if "right_type" in operator:
                    result.append(
                        f'{correct_type(operator["return_type"])} {class_name}::operator{operator["name"]}({type_for_parameter(operator["right_type"])}other) const {{'
                    )
                    (encode, arg_name) = get_encoded_arg("other", operator["right_type"], None)
                    result += encode
                    result.append(
                        f'\treturn internal::_call_builtin_operator_ptr<{get_gdextension_type(correct_type(operator["return_type"]))}>(_method_bindings.operator_{get_operator_id_name(operator["name"])}_{operator["right_type"]}, (GDExtensionConstTypePtr)&opaque, (GDExtensionConstTypePtr){arg_name});'
                    )
                    result.append("}")
                else:
                    result.append(
                        f'{correct_type(operator["return_type"])} {class_name}::operator{operator["name"].replace("unary", "")}() const {{'
                    )
                    result.append(
                        f'\treturn internal::_call_builtin_operator_ptr<{get_gdextension_type(correct_type(operator["return_type"]))}>(_method_bindings.operator_{get_operator_id_name(operator["name"])}, (GDExtensionConstTypePtr)&opaque, (GDExtensionConstTypePtr)nullptr);'
                    )
                    result.append("}")
                result.append("")

    # Copy assignment.
    if copy_constructor_index >= 0:
        result.append(f"{class_name} &{class_name}::operator=(const {class_name} &other) {{")
        if builtin_api["has_destructor"]:
            result.append("\t_method_bindings.destructor(&opaque);")
        (encode, arg_name) = get_encoded_arg(
            "other",
            class_name,
            None,
        )
        result += encode
        result.append(
            f"\tinternal::_call_builtin_constructor(_method_bindings.constructor_{copy_constructor_index}, &opaque, {arg_name});"
        )
        result.append("\treturn *this;")
        result.append("}")
        result.append("")

    # Move assignment.
    result.append(f"{class_name} &{class_name}::operator=({class_name} &&other) {{")
    if needs_copy_instead_of_move(class_name) and copy_constructor_index >= 0:
        result.append(
            f"\tinternal::_call_builtin_constructor(_method_bindings.constructor_{copy_constructor_index}, &opaque, &other);"
        )
    else:
        result.append("\tstd::swap(opaque, other.opaque);")
    result.append("\treturn *this;")
    result.append("}")

    result.append("")
    result.append("} //namespace godot")

    return "\n".join(result)


def generate_engine_classes_bindings(api, output_dir, use_template_get_node):
    global engine_classes
    global singletons
    global native_structures

    include_gen_folder = Path(output_dir) / "include" / "godot_cpp" / "classes"
    source_gen_folder = Path(output_dir) / "src" / "classes"

    include_gen_folder.mkdir(parents=True, exist_ok=True)
    source_gen_folder.mkdir(parents=True, exist_ok=True)

    # First create map of classes and singletons.
    for class_api in api["classes"]:
        # Generate code for the ClassDB singleton under a different name.
        if class_api["name"] == "ClassDB":
            class_api["name"] = "ClassDBSingleton"
            class_api["alias_for"] = "ClassDB"
        engine_classes[class_api["name"]] = class_api["is_refcounted"]
    for native_struct in api["native_structures"]:
        engine_classes[native_struct["name"]] = False
        native_structures.append(native_struct["name"])

    for singleton in api["singletons"]:
        # Generate code for the ClassDB singleton under a different name.
        if singleton["name"] == "ClassDB":
            singleton["name"] = "ClassDBSingleton"
            singleton["alias_for"] = "ClassDB"
        singletons.append(singleton["name"])

    for class_api in api["classes"]:
        # Check used classes for header include.
        used_classes = set()
        fully_used_classes = set()

        class_name = class_api["name"]

        header_filename = include_gen_folder / (camel_to_snake(class_api["name"]) + ".hpp")
        source_filename = source_gen_folder / (camel_to_snake(class_api["name"]) + ".cpp")

        if "methods" in class_api:
            for method in class_api["methods"]:
                if "arguments" in method:
                    for argument in method["arguments"]:
                        type_name = argument["type"]
                        if type_name.startswith("const "):
                            type_name = type_name[6:]
                        if type_name.endswith("*"):
                            type_name = type_name[:-1]
                        if is_included(type_name, class_name):
                            if type_name.startswith("typedarray::"):
                                fully_used_classes.add("TypedArray")
                                array_type_name = type_name.replace("typedarray::", "")
                                if array_type_name.startswith("const "):
                                    array_type_name = array_type_name[6:]
                                if array_type_name.endswith("*"):
                                    array_type_name = array_type_name[:-1]
                                if is_included(array_type_name, class_name):
                                    if is_enum(array_type_name):
                                        fully_used_classes.add(get_enum_class(array_type_name))
                                    elif "default_value" in argument:
                                        fully_used_classes.add(array_type_name)
                                    else:
                                        used_classes.add(array_type_name)
                            elif is_enum(type_name):
                                fully_used_classes.add(get_enum_class(type_name))
                            elif "default_value" in argument:
                                fully_used_classes.add(type_name)
                            else:
                                used_classes.add(type_name)
                            if is_refcounted(type_name):
                                fully_used_classes.add("Ref")
                if "return_value" in method:
                    type_name = method["return_value"]["type"]
                    if type_name.startswith("const "):
                        type_name = type_name[6:]
                    if type_name.endswith("*"):
                        type_name = type_name[:-1]
                    if is_included(type_name, class_name):
                        if type_name.startswith("typedarray::"):
                            fully_used_classes.add("TypedArray")
                            array_type_name = type_name.replace("typedarray::", "")
                            if array_type_name.startswith("const "):
                                array_type_name = array_type_name[6:]
                            if array_type_name.endswith("*"):
                                array_type_name = array_type_name[:-1]
                            if is_included(array_type_name, class_name):
                                if is_enum(array_type_name):
                                    fully_used_classes.add(get_enum_class(array_type_name))
                                elif is_variant(array_type_name):
                                    fully_used_classes.add(array_type_name)
                                else:
                                    used_classes.add(array_type_name)
                        elif is_enum(type_name):
                            fully_used_classes.add(get_enum_class(type_name))
                        elif is_variant(type_name):
                            fully_used_classes.add(type_name)
                        else:
                            used_classes.add(type_name)
                        if is_refcounted(type_name):
                            fully_used_classes.add("Ref")

        if "members" in class_api:
            for member in class_api["members"]:
                if is_included(member["type"], class_name):
                    if is_enum(member["type"]):
                        fully_used_classes.add(get_enum_class(member["type"]))
                    else:
                        used_classes.add(member["type"])
                    if is_refcounted(member["type"]):
                        fully_used_classes.add("Ref")

        if "inherits" in class_api:
            if is_included(class_api["inherits"], class_name):
                fully_used_classes.add(class_api["inherits"])
            if is_refcounted(class_api["name"]):
                fully_used_classes.add("Ref")
        else:
            fully_used_classes.add("Wrapped")

        # In order to ensure that PtrToArg specializations for native structs are
        # always used, let's move any of them into 'fully_used_classes'.
        for type_name in used_classes:
            if is_struct_type(type_name) and not is_included_struct_type(type_name):
                fully_used_classes.add(type_name)

        for type_name in fully_used_classes:
            if type_name in used_classes:
                used_classes.remove(type_name)

        used_classes = list(used_classes)
        used_classes.sort()
        fully_used_classes = list(fully_used_classes)
        fully_used_classes.sort()

        with header_filename.open("w+", encoding="utf-8") as header_file:
            header_file.write(
                generate_engine_class_header(class_api, used_classes, fully_used_classes, use_template_get_node)
            )

        with source_filename.open("w+", encoding="utf-8") as source_file:
            source_file.write(
                generate_engine_class_source(class_api, used_classes, fully_used_classes, use_template_get_node)
            )

    for native_struct in api["native_structures"]:
        struct_name = native_struct["name"]
        snake_struct_name = camel_to_snake(struct_name)

        header_filename = include_gen_folder / (snake_struct_name + ".hpp")

        result = []
        add_header(f"{snake_struct_name}.hpp", result)

        header_guard = f"GODOT_CPP_{snake_struct_name.upper()}_HPP"
        result.append(f"#ifndef {header_guard}")
        result.append(f"#define {header_guard}")

        used_classes = []
        expanded_format = native_struct["format"].replace("(", " ").replace(")", ";").replace(",", ";")
        for field in expanded_format.split(";"):
            field_type = field.strip().split(" ")[0].split("::")[0]
            if field_type != "" and not is_included_type(field_type) and not is_pod_type(field_type):
                if not field_type in used_classes:
                    used_classes.append(field_type)

        result.append("")

        for included in used_classes:
            result.append(f"#include <godot_cpp/{get_include_path(included)}>")

        if len(used_classes) == 0:
            result.append("#include <godot_cpp/core/method_ptrcall.hpp>")
        result.append("")

        result.append("namespace godot {")
        result.append("")

        result.append(f"struct {struct_name} {{")
        for field in native_struct["format"].split(";"):
            if field != "":
                result.append(f"\t{field};")
        result.append("};")

        result.append("")
        result.append(f"GDVIRTUAL_NATIVE_PTR({struct_name});")
        result.append("")
        result.append("} // namespace godot")
        result.append("")
        result.append(f"#endif // ! {header_guard}")

        with header_filename.open("w+", encoding="utf-8") as header_file:
            header_file.write("\n".join(result))


def generate_engine_class_header(class_api, used_classes, fully_used_classes, use_template_get_node):
    global singletons
    result = []

    class_name = class_api["name"]
    snake_class_name = camel_to_snake(class_name).upper()
    is_singleton = class_name in singletons

    add_header(f"{snake_class_name.lower()}.hpp", result)

    header_guard = f"GODOT_CPP_{snake_class_name}_HPP"

    result.append(f"#ifndef {header_guard}")
    result.append(f"#define {header_guard}")

    result.append("")

    for included in fully_used_classes:
        if included == "TypedArray":
            result.append("#include <godot_cpp/variant/typed_array.hpp>")
        else:
            result.append(f"#include <godot_cpp/{get_include_path(included)}>")

    if class_name == "EditorPlugin":
        result.append("#include <godot_cpp/classes/editor_plugin_registration.hpp>")

    if len(fully_used_classes) > 0:
        result.append("")

    if class_name != "Object" and class_name != "ClassDBSingleton":
        result.append("#include <godot_cpp/core/class_db.hpp>")
        result.append("")
        result.append("#include <type_traits>")
        result.append("")

    result.append("namespace godot {")
    result.append("")

    for type_name in used_classes:
        if is_struct_type(type_name):
            result.append(f"struct {type_name};")
        else:
            result.append(f"class {type_name};")

    if len(used_classes) > 0:
        result.append("")

    inherits = class_api["inherits"] if "inherits" in class_api else "Wrapped"
    result.append(f"class {class_name} : public {inherits} {{")

    if "alias_for" in class_api:
        result.append(f"\tGDEXTENSION_CLASS_ALIAS({class_name}, {class_api['alias_for']}, {inherits})")
    else:
        result.append(f"\tGDEXTENSION_CLASS({class_name}, {inherits})")
    result.append("")

    result.append("public:")
    result.append("")

    if "enums" in class_api:
        for enum_api in class_api["enums"]:
            result.append(f'\tenum {enum_api["name"]} {{')
            for value in enum_api["values"]:
                result.append(f'\t\t{value["name"]} = {value["value"]},')
            result.append("\t};")
            result.append("")

    if "constants" in class_api:
        for value in class_api["constants"]:
            if "type" not in value:
                value["type"] = "int"
            result.append(f'\tstatic const {value["type"]} {value["name"]} = {value["value"]};')
        result.append("")

    if is_singleton:
        result.append(f"\tstatic {class_name} *get_singleton();")
        result.append("")

    if "methods" in class_api:
        for method in class_api["methods"]:
            if method["is_virtual"]:
                # Will be done later.
                continue

            vararg = "is_vararg" in method and method["is_vararg"]

            method_signature = "\t"
            if vararg:
                method_signature += "private: "
            method_signature += make_signature(
                class_name, method, for_header=True, use_template_get_node=use_template_get_node
            )
            result.append(method_signature + ";")

            if vararg:
                # Add templated version.
                result += make_varargs_template(method)

        # Virtuals now.
        for method in class_api["methods"]:
            if not method["is_virtual"]:
                continue

            method_signature = "\t"
            method_signature += make_signature(
                class_name, method, for_header=True, use_template_get_node=use_template_get_node
            )
            result.append(method_signature + ";")

    result.append("protected:")
    # T is the custom class we want to register (from which the call initiates, going up the inheritance chain),
    # B is its base class (can be a custom class too, that's why we pass it).
    result.append("\ttemplate <class T, class B>")
    result.append("\tstatic void register_virtuals() {")
    if class_name != "Object":
        result.append(f"\t\t{inherits}::register_virtuals<T, B>();")
        if "methods" in class_api:
            for method in class_api["methods"]:
                if not method["is_virtual"]:
                    continue
                method_name = escape_identifier(method["name"])
                result.append(
                    # If the method is different from the base class, it means T overrides it, so it needs to be bound.
                    # Note that with an `if constexpr`, the code inside the `if` will not even be compiled if the
                    # condition returns false (in such cases it can't compile due to ambiguity).
                    f"\t\tif constexpr (!std::is_same_v<decltype(&B::{method_name}),decltype(&T::{method_name})>) {{"
                )
                result.append(f"\t\t\tBIND_VIRTUAL_METHOD(T, {method_name});")
                result.append("\t\t}")

    result.append("\t}")
    result.append("")
    result.append("public:")

    # Special cases.
    if class_name == "XMLParser":
        result.append("\tError _open_buffer(const uint8_t *p_buffer, size_t p_size);")

    if class_name == "FileAccess":
        result.append("\tuint64_t get_buffer(uint8_t *p_dst, uint64_t p_length) const;")
        result.append("\tvoid store_buffer(const uint8_t *p_src, uint64_t p_length);")

    if class_name == "WorkerThreadPool":
        result.append("\tenum {")
        result.append("\tINVALID_TASK_ID = -1")
        result.append("\t};")
        result.append("\ttypedef int64_t TaskID;")
        result.append("\ttypedef int64_t GroupID;")
        result.append(
            "\tTaskID add_native_task(void (*p_func)(void *), void *p_userdata, bool p_high_priority = false, const String &p_description = String());"
        )
        result.append(
            "\tGroupID add_native_group_task(void (*p_func)(void *, uint32_t), void *p_userdata, int p_elements, int p_tasks = -1, bool p_high_priority = false, const String &p_description = String());"
        )

    if class_name == "Object":
        result.append("")

        result.append("\ttemplate<class T>")
        result.append("\tstatic T *cast_to(Object *p_object);")

        result.append("\ttemplate<class T>")
        result.append("\tstatic const T *cast_to(const Object *p_object);")

        result.append("\tvirtual ~Object() = default;")

    elif use_template_get_node and class_name == "Node":
        result.append("\ttemplate<class T>")
        result.append(
            "\tT *get_node(const NodePath &p_path) const { return Object::cast_to<T>(get_node_internal(p_path)); }"
        )

    result.append("")
    result.append("};")
    result.append("")

    result.append("} // namespace godot")
    result.append("")

    if "enums" in class_api and class_name != "Object":
        for enum_api in class_api["enums"]:
            if enum_api["is_bitfield"]:
                result.append(f'VARIANT_BITFIELD_CAST({class_name}::{enum_api["name"]});')
            else:
                result.append(f'VARIANT_ENUM_CAST({class_name}::{enum_api["name"]});')
        result.append("")

    if class_name == "ClassDBSingleton":
        result.append("#define CLASSDB_SINGLETON_FORWARD_METHODS \\")
        for method in class_api["methods"]:
            # ClassDBSingleton shouldn't have any static or vararg methods, but if some appear later, lets skip them.
            if vararg:
                continue
            if "is_static" in method and method["is_static"]:
                continue

            method_signature = "\tstatic "
            if "return_type" in method:
                method_signature += f'{correct_type(method["return_type"])} '
            elif "return_value" in method:
                method_signature += (
                    correct_type(method["return_value"]["type"], method["return_value"].get("meta", None)) + " "
                )
            else:
                method_signature += "void "

            method_signature += f'{method["name"]}('

            method_arguments = []
            if "arguments" in method:
                method_arguments = method["arguments"]

            method_signature += make_function_parameters(
                method_arguments, include_default=True, for_builtin=True, is_vararg=False
            )

            method_signature += ") { \\"

            result.append(method_signature)

            method_body = "\t\t"
            if "return_type" in method or "return_value" in method:
                method_body += "return "
            method_body += f'ClassDBSingleton::get_singleton()->{method["name"]}('
            method_body += ", ".join(map(lambda x: escape_identifier(x["name"]), method_arguments))
            method_body += "); \\"

            result.append(method_body)
            result.append("\t} \\")
        result.append("\t;")
        result.append("")

    result.append(f"#endif // ! {header_guard}")

    return "\n".join(result)


def generate_engine_class_source(class_api, used_classes, fully_used_classes, use_template_get_node):
    global singletons
    result = []

    class_name = class_api["name"]
    snake_class_name = camel_to_snake(class_name)
    is_singleton = class_name in singletons

    add_header(f"{snake_class_name}.cpp", result)

    result.append(f"#include <godot_cpp/classes/{snake_class_name}.hpp>")
    result.append("")
    result.append("#include <godot_cpp/core/engine_ptrcall.hpp>")
    result.append("#include <godot_cpp/core/error_macros.hpp>")
    result.append("")

    for included in used_classes:
        result.append(f"#include <godot_cpp/{get_include_path(included)}>")

    if len(used_classes) > 0:
        result.append("")

    result.append("namespace godot {")
    result.append("")

    if is_singleton:
        result.append(f"{class_name} *{class_name}::get_singleton() {{")
        # We assume multi-threaded access is OK because each assignment will assign the same value every time
        result.append(f"\tstatic {class_name} *singleton = nullptr;")
        result.append("\tif (unlikely(singleton == nullptr)) {")
        result.append(
            f"\t\tGDExtensionObjectPtr singleton_obj = internal::gdextension_interface_global_get_singleton({class_name}::get_class_static()._native_ptr());"
        )
        result.append("#ifdef DEBUG_ENABLED")
        result.append("\t\tERR_FAIL_NULL_V(singleton_obj, nullptr);")
        result.append("#endif // DEBUG_ENABLED")
        result.append(
            f"\t\tsingleton = reinterpret_cast<{class_name} *>(internal::gdextension_interface_object_get_instance_binding(singleton_obj, internal::token, &{class_name}::_gde_binding_callbacks));"
        )
        result.append("#ifdef DEBUG_ENABLED")
        result.append("\t\tERR_FAIL_NULL_V(singleton, nullptr);")
        result.append("#endif // DEBUG_ENABLED")
        result.append("\t}")
        result.append("\treturn singleton;")
        result.append("}")
        result.append("")

    if "methods" in class_api:
        for method in class_api["methods"]:
            if method["is_virtual"]:
                # Will be done later
                continue

            vararg = "is_vararg" in method and method["is_vararg"]

            # Method signature.
            method_signature = make_signature(class_name, method, use_template_get_node=use_template_get_node)
            result.append(method_signature + " {")

            # Method body.
            result.append(
                f'\tstatic GDExtensionMethodBindPtr _gde_method_bind = internal::gdextension_interface_classdb_get_method_bind({class_name}::get_class_static()._native_ptr(), StringName("{method["name"]}")._native_ptr(), {method["hash"]});'
            )
            method_call = "\t"
            has_return = "return_value" in method and method["return_value"]["type"] != "void"

            if has_return:
                result.append(
                    f'\tCHECK_METHOD_BIND_RET(_gde_method_bind, {get_default_value_for_type(method["return_value"]["type"])});'
                )
            else:
                result.append("\tCHECK_METHOD_BIND(_gde_method_bind);")

            is_ref = False
            if not vararg:
                if has_return:
                    return_type = method["return_value"]["type"]
                    meta_type = method["return_value"]["meta"] if "meta" in method["return_value"] else None
                    if is_enum(return_type):
                        if method["is_static"]:
                            method_call += f"return ({get_gdextension_type(correct_type(return_type, meta_type))})internal::_call_native_mb_ret<int64_t>(_gde_method_bind, nullptr"
                        else:
                            method_call += f"return ({get_gdextension_type(correct_type(return_type, meta_type))})internal::_call_native_mb_ret<int64_t>(_gde_method_bind, _owner"
                    elif is_pod_type(return_type) or is_variant(return_type):
                        if method["is_static"]:
                            method_call += f"return internal::_call_native_mb_ret<{get_gdextension_type(correct_type(return_type, meta_type))}>(_gde_method_bind, nullptr"
                        else:
                            method_call += f"return internal::_call_native_mb_ret<{get_gdextension_type(correct_type(return_type, meta_type))}>(_gde_method_bind, _owner"
                    elif is_refcounted(return_type):
                        if method["is_static"]:
                            method_call += f"return Ref<{return_type}>::_gde_internal_constructor(internal::_call_native_mb_ret_obj<{return_type}>(_gde_method_bind, nullptr"
                        else:
                            method_call += f"return Ref<{return_type}>::_gde_internal_constructor(internal::_call_native_mb_ret_obj<{return_type}>(_gde_method_bind, _owner"
                        is_ref = True
                    else:
                        if method["is_static"]:
                            method_call += (
                                f"return internal::_call_native_mb_ret_obj<{return_type}>(_gde_method_bind, nullptr"
                            )
                        else:
                            method_call += (
                                f"return internal::_call_native_mb_ret_obj<{return_type}>(_gde_method_bind, _owner"
                            )
                else:
                    if method["is_static"]:
                        method_call += "internal::_call_native_mb_no_ret(_gde_method_bind, nullptr"
                    else:
                        method_call += "internal::_call_native_mb_no_ret(_gde_method_bind, _owner"

                if "arguments" in method:
                    method_call += ", "
                    arguments = []
                    for argument in method["arguments"]:
                        (encode, arg_name) = get_encoded_arg(
                            argument["name"],
                            argument["type"],
                            argument["meta"] if "meta" in argument else None,
                        )
                        result += encode
                        arguments.append(arg_name)
                    method_call += ", ".join(arguments)
            else:  # vararg.
                result.append("\tGDExtensionCallError error;")
                result.append("\tVariant ret;")
                method_call += "internal::gdextension_interface_object_method_bind_call(_gde_method_bind, _owner, reinterpret_cast<GDExtensionConstVariantPtr *>(args), arg_count, &ret, &error"

            if is_ref:
                method_call += ")"  # Close Ref<> constructor.
            method_call += ");"
            result.append(method_call)

            if vararg and ("return_value" in method and method["return_value"]["type"] != "void"):
                return_type = get_enum_fullname(method["return_value"]["type"])
                if return_type != "Variant":
                    result.append(f"\treturn VariantCaster<{return_type}>::cast(ret);")
                else:
                    result.append("\treturn ret;")

            result.append("}")
            result.append("")

        # Virtuals now.
        for method in class_api["methods"]:
            if not method["is_virtual"]:
                continue

            method_signature = make_signature(class_name, method, use_template_get_node=use_template_get_node)
            method_signature += " {"
            if "return_value" in method and correct_type(method["return_value"]["type"]) != "void":
                result.append(method_signature)
                result.append(f'\treturn {get_default_value_for_type(method["return_value"]["type"])};')
                result.append("}")
            else:
                method_signature += "}"
                result.append(method_signature)
            result.append("")

    result.append("")
    result.append("} // namespace godot ")

    return "\n".join(result)


def generate_global_constants(api, output_dir):
    include_gen_folder = Path(output_dir) / "include" / "godot_cpp" / "classes"
    source_gen_folder = Path(output_dir) / "src" / "classes"

    include_gen_folder.mkdir(parents=True, exist_ok=True)
    source_gen_folder.mkdir(parents=True, exist_ok=True)

    # Generate header

    header = []
    add_header("global_constants.hpp", header)

    header_filename = include_gen_folder / "global_constants.hpp"

    header_guard = "GODOT_CPP_GLOBAL_CONSTANTS_HPP"
    header.append(f"#ifndef {header_guard}")
    header.append(f"#define {header_guard}")
    header.append("")
    header.append("namespace godot {")
    header.append("")

    for constant in api["global_constants"]:
        header.append(f'\tconst int {escape_identifier(constant["name"])} = {constant["value"]};')

    header.append("")

    for enum_def in api["global_enums"]:
        if enum_def["name"].startswith("Variant."):
            continue

        header.append(f'\tenum {enum_def["name"]} {{')
        for value in enum_def["values"]:
            header.append(f'\t\t{value["name"]} = {value["value"]},')
        header.append("\t};")
        header.append("")

    header.append("} // namespace godot")

    header.append("")
    header.append(f"#endif // ! {header_guard}")

    with header_filename.open("w+", encoding="utf-8") as header_file:
        header_file.write("\n".join(header))


def generate_version_header(api, output_dir):
    header = []
    header_filename = "version.hpp"
    add_header(header_filename, header)

    include_gen_folder = Path(output_dir) / "include" / "godot_cpp" / "core"
    include_gen_folder.mkdir(parents=True, exist_ok=True)

    header_file_path = include_gen_folder / header_filename

    header_guard = "GODOT_CPP_VERSION_HPP"
    header.append(f"#ifndef {header_guard}")
    header.append(f"#define {header_guard}")
    header.append("")

    header.append(f"#define GODOT_VERSION_MAJOR {api['header']['version_major']}")
    header.append(f"#define GODOT_VERSION_MINOR {api['header']['version_minor']}")
    header.append(f"#define GODOT_VERSION_PATCH {api['header']['version_patch']}")
    header.append(f"#define GODOT_VERSION_STATUS \"{api['header']['version_status']}\"")
    header.append(f"#define GODOT_VERSION_BUILD \"{api['header']['version_build']}\"")

    header.append("")
    header.append(f"#endif // {header_guard}")
    header.append("")

    with header_file_path.open("w+", encoding="utf-8") as header_file:
        header_file.write("\n".join(header))


def generate_global_constant_binds(api, output_dir):
    include_gen_folder = Path(output_dir) / "include" / "godot_cpp" / "classes"
    source_gen_folder = Path(output_dir) / "src" / "classes"

    include_gen_folder.mkdir(parents=True, exist_ok=True)
    source_gen_folder.mkdir(parents=True, exist_ok=True)

    # Generate header

    header = []
    add_header("global_constants_binds.hpp", header)

    header_filename = include_gen_folder / "global_constants_binds.hpp"

    header_guard = "GODOT_CPP_GLOBAL_CONSTANTS_BINDS_HPP"
    header.append(f"#ifndef {header_guard}")
    header.append(f"#define {header_guard}")
    header.append("")
    header.append("#include <godot_cpp/classes/global_constants.hpp>")
    header.append("")

    for enum_def in api["global_enums"]:
        if enum_def["name"].startswith("Variant."):
            continue

        if enum_def["is_bitfield"]:
            header.append(f'VARIANT_BITFIELD_CAST({enum_def["name"]});')
        else:
            header.append(f'VARIANT_ENUM_CAST({enum_def["name"]});')

    # Variant::Type is not a global enum, but only one line, it is worth to place in this file instead of creating new file.
    header.append(f"VARIANT_ENUM_CAST(godot::Variant::Type);")

    header.append("")

    header.append(f"#endif // ! {header_guard}")

    with header_filename.open("w+", encoding="utf-8") as header_file:
        header_file.write("\n".join(header))


def generate_utility_functions(api, output_dir):
    include_gen_folder = Path(output_dir) / "include" / "godot_cpp" / "variant"
    source_gen_folder = Path(output_dir) / "src" / "variant"

    include_gen_folder.mkdir(parents=True, exist_ok=True)
    source_gen_folder.mkdir(parents=True, exist_ok=True)

    # Generate header.

    header = []
    add_header("utility_functions.hpp", header)

    header_filename = include_gen_folder / "utility_functions.hpp"

    header_guard = "GODOT_CPP_UTILITY_FUNCTIONS_HPP"
    header.append(f"#ifndef {header_guard}")
    header.append(f"#define {header_guard}")
    header.append("")
    header.append("#include <godot_cpp/variant/builtin_types.hpp>")
    header.append("#include <godot_cpp/variant/variant.hpp>")
    header.append("")
    header.append("#include <array>")
    header.append("")
    header.append("namespace godot {")
    header.append("")
    header.append("class UtilityFunctions {")
    header.append("public:")

    for function in api["utility_functions"]:
        vararg = "is_vararg" in function and function["is_vararg"]

        function_signature = "\t"
        function_signature += make_signature("UtilityFunctions", function, for_header=True, static=True)
        header.append(function_signature + ";")

        if vararg:
            # Add templated version.
            header += make_varargs_template(function, static=True)

    header.append("};")
    header.append("")
    header.append("} // namespace godot")
    header.append("")
    header.append(f"#endif // ! {header_guard}")

    with header_filename.open("w+", encoding="utf-8") as header_file:
        header_file.write("\n".join(header))

    # Generate source.

    source = []
    add_header("utility_functions.cpp", source)
    source_filename = source_gen_folder / "utility_functions.cpp"

    source.append("#include <godot_cpp/variant/utility_functions.hpp>")
    source.append("")
    source.append("#include <godot_cpp/core/error_macros.hpp>")
    source.append("#include <godot_cpp/core/engine_ptrcall.hpp>")
    source.append("")
    source.append("namespace godot {")
    source.append("")

    for function in api["utility_functions"]:
        vararg = "is_vararg" in function and function["is_vararg"]

        function_signature = make_signature("UtilityFunctions", function)
        source.append(function_signature + " {")

        # Function body.

        source.append(
            f'\tstatic GDExtensionPtrUtilityFunction _gde_function = internal::gdextension_interface_variant_get_ptr_utility_function(StringName("{function["name"]}")._native_ptr(), {function["hash"]});'
        )
        has_return = "return_type" in function and function["return_type"] != "void"
        if has_return:
            source.append(
                f'\tCHECK_METHOD_BIND_RET(_gde_function, {get_default_value_for_type(function["return_type"])});'
            )
        else:
            source.append("\tCHECK_METHOD_BIND(_gde_function);")

        function_call = "\t"
        if not vararg:
            if has_return:
                function_call += "return "
                if function["return_type"] == "Object":
                    function_call += "internal::_call_utility_ret_obj(_gde_function"
                else:
                    function_call += f'internal::_call_utility_ret<{get_gdextension_type(correct_type(function["return_type"]))}>(_gde_function'
            else:
                function_call += "internal::_call_utility_no_ret(_gde_function"

            if "arguments" in function:
                function_call += ", "
                arguments = []
                for argument in function["arguments"]:
                    (encode, arg_name) = get_encoded_arg(
                        argument["name"],
                        argument["type"],
                        argument["meta"] if "meta" in argument else None,
                    )
                    source += encode
                    arguments.append(arg_name)
                function_call += ", ".join(arguments)
        else:
            if has_return:
                source.append(f'\t{get_gdextension_type(correct_type(function["return_type"]))} ret;')
            else:
                source.append("\tVariant ret;")
            function_call += "_gde_function(&ret, reinterpret_cast<GDExtensionConstVariantPtr *>(args), arg_count"

        function_call += ");"
        source.append(function_call)

        if vararg and has_return:
            source.append("\treturn ret;")

        source.append("}")
        source.append("")

    source.append("} // namespace godot")

    with source_filename.open("w+", encoding="utf-8") as source_file:
        source_file.write("\n".join(source))


# Helper functions.


def camel_to_snake(name):
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    name = re.sub("([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.replace("2_D", "2D").replace("3_D", "3D").lower()


def make_function_parameters(parameters, include_default=False, for_builtin=False, is_vararg=False):
    signature = []

    for index, par in enumerate(parameters):
        parameter = type_for_parameter(par["type"], par["meta"] if "meta" in par else None)
        parameter_name = escape_identifier(par["name"])
        if len(parameter_name) == 0:
            parameter_name = "arg_" + str(index + 1)
        parameter += parameter_name

        if include_default and "default_value" in par and (not for_builtin or par["type"] != "Variant"):
            parameter += " = "
            if is_enum(par["type"]):
                parameter_type = correct_type(par["type"])
                if parameter_type == "void":
                    parameter_type = "Variant"
                parameter += f"({parameter_type})"
            parameter += correct_default_value(par["default_value"], par["type"])
        signature.append(parameter)

    if is_vararg:
        signature.append("const Args&... args")

    return ", ".join(signature)


def type_for_parameter(type_name, meta=None):
    if type_name == "void":
        return "Variant "
    elif is_pod_type(type_name) and type_name != "Nil" or is_enum(type_name):
        return f"{correct_type(type_name, meta)} "
    elif is_variant(type_name) or is_refcounted(type_name):
        return f"const {correct_type(type_name)} &"
    else:
        return f"{correct_type(type_name)}"


def get_include_path(type_name):
    base_dir = ""
    if type_name == "Object":
        base_dir = "core"
    elif is_variant(type_name):
        base_dir = "variant"
    else:
        base_dir = "classes"

    return f"{base_dir}/{camel_to_snake(type_name)}.hpp"


def get_encoded_arg(arg_name, type_name, type_meta):
    result = []

    name = escape_identifier(arg_name)
    arg_type = correct_type(type_name)
    if is_pod_type(arg_type):
        result.append(f"\t{get_gdextension_type(arg_type)} {name}_encoded;")
        result.append(f"\tPtrToArg<{correct_type(type_name)}>::encode({name}, &{name}_encoded);")
        name = f"&{name}_encoded"
    elif is_engine_class(type_name):
        # `{name}` is a C++ wrapper, it contains a field which is the object's pointer Godot expects.
        # We have to check `nullptr` because when the caller sends `nullptr`, the wrapper itself will be null.
        name = f"({name} != nullptr ? &{name}->_owner : nullptr)"
    else:
        name = f"&{name}"

    return (result, name)


def make_signature(
    class_name, function_data, for_header=False, use_template_get_node=True, for_builtin=False, static=False
):
    function_signature = ""

    is_vararg = "is_vararg" in function_data and function_data["is_vararg"]

    if for_header:
        if "is_virtual" in function_data and function_data["is_virtual"]:
            function_signature += "virtual "

        if is_vararg:
            function_signature += "private: "

        if static:
            function_signature += "static "

    return_type = "void"
    return_meta = None
    if "return_type" in function_data:
        return_type = correct_type(function_data["return_type"])
    elif "return_value" in function_data:
        return_type = function_data["return_value"]["type"]
        return_meta = function_data["return_value"]["meta"] if "meta" in function_data["return_value"] else None

    function_signature += correct_type(
        return_type,
        return_meta,
    )

    if not function_signature.endswith("*"):
        function_signature += " "

    if not for_header:
        function_signature += f"{class_name}::"

    function_signature += escape_identifier(function_data["name"])

    if is_vararg or (
        not for_builtin and use_template_get_node and class_name == "Node" and function_data["name"] == "get_node"
    ):
        function_signature += "_internal"

    function_signature += "("

    arguments = function_data["arguments"] if "arguments" in function_data else []

    if not is_vararg:
        function_signature += make_function_parameters(arguments, for_header, for_builtin, is_vararg)
    else:
        function_signature += "const Variant **args, GDExtensionInt arg_count"

    function_signature += ")"

    if "is_static" in function_data and function_data["is_static"] and for_header:
        function_signature = "static " + function_signature
    elif "is_const" in function_data and function_data["is_const"]:
        function_signature += " const"

    return function_signature


def make_varargs_template(function_data, static=False):
    result = []

    function_signature = "\tpublic: template<class... Args> "

    if static:
        function_signature += "static "

    return_type = "void"
    return_meta = None
    if "return_type" in function_data:
        return_type = correct_type(function_data["return_type"])
    elif "return_value" in function_data:
        return_type = function_data["return_value"]["type"]
        return_meta = function_data["return_value"]["meta"] if "meta" in function_data["return_value"] else None

    function_signature += correct_type(
        return_type,
        return_meta,
    )

    if not function_signature.endswith("*"):
        function_signature += " "

    function_signature += f'{escape_identifier(function_data["name"])}'

    method_arguments = []
    if "arguments" in function_data:
        method_arguments = function_data["arguments"]

    function_signature += "("

    is_vararg = "is_vararg" in function_data and function_data["is_vararg"]

    function_signature += make_function_parameters(method_arguments, include_default=True, is_vararg=is_vararg)

    function_signature += ")"

    if "is_const" in function_data and function_data["is_const"]:
        function_signature += " const"

    function_signature += " {"
    result.append(function_signature)

    args_array = f"\t\tstd::array<Variant, {len(method_arguments)} + sizeof...(Args)> variant_args {{ "
    for argument in method_arguments:
        if argument["type"] == "Variant":
            args_array += argument["name"]
        else:
            args_array += f'Variant({argument["name"]})'
        args_array += ", "

    args_array += "Variant(args)... };"
    result.append(args_array)
    result.append(f"\t\tstd::array<const Variant *, {len(method_arguments)} + sizeof...(Args)> call_args;")
    result.append("\t\tfor(size_t i = 0; i < variant_args.size(); i++) {")
    result.append("\t\t\tcall_args[i] = &variant_args[i];")
    result.append("\t\t}")

    call_line = "\t\t"

    if return_type != "void":
        call_line += "return "

    call_line += f'{escape_identifier(function_data["name"])}_internal(call_args.data(), variant_args.size());'
    result.append(call_line)
    result.append("\t}")

    return result


# Engine idiosyncrasies.


def is_pod_type(type_name):
    """
    Those are types for which no class should be generated.
    """
    return type_name in [
        "Nil",
        "void",
        "bool",
        "real_t",
        "float",
        "double",
        "int",
        "int8_t",
        "uint8_t",
        "int16_t",
        "uint16_t",
        "int32_t",
        "int64_t",
        "uint32_t",
        "uint64_t",
    ]


def is_included_type(type_name):
    # Types which we already have implemented.
    return is_included_struct_type(type_name) or type_name in ["ObjectID"]


def is_included_struct_type(type_name):
    # Struct types which we already have implemented.
    return type_name in [
        "AABB",
        "Basis",
        "Color",
        "Plane",
        "Projection",
        "Quaternion",
        "Rect2",
        "Rect2i",
        "Transform2D",
        "Transform3D",
        "Vector2",
        "Vector2i",
        "Vector3",
        "Vector3i",
        "Vector4",
        "Vector4i",
    ]


def is_packed_array(type_name):
    """
    Those are types for which we add our extra packed array functions.
    """
    return type_name in [
        "PackedByteArray",
        "PackedColorArray",
        "PackedFloat32Array",
        "PackedFloat64Array",
        "PackedInt32Array",
        "PackedInt64Array",
        "PackedStringArray",
        "PackedVector2Array",
        "PackedVector3Array",
    ]


def needs_copy_instead_of_move(type_name):
    """
    Those are types which need initialised data or we'll get warning spam so need a copy instead of move.
    """
    return type_name in [
        "Dictionary",
    ]


def is_enum(type_name):
    return type_name.startswith("enum::") or type_name.startswith("bitfield::")


def is_bitfield(type_name):
    return type_name.startswith("bitfield::")


def get_enum_class(enum_name: str):
    if "." in enum_name:
        if is_bitfield(enum_name):
            return enum_name.replace("bitfield::", "").split(".")[0]
        else:
            return enum_name.replace("enum::", "").split(".")[0]
    else:
        return "GlobalConstants"


def get_enum_fullname(enum_name: str):
    if is_bitfield(enum_name):
        return enum_name.replace("bitfield::", "BitField<") + ">"
    else:
        return enum_name.replace("enum::", "")


def get_enum_name(enum_name: str):
    if is_bitfield(enum_name):
        return enum_name.replace("bitfield::", "").split(".")[-1]
    else:
        return enum_name.replace("enum::", "").split(".")[-1]


def is_variant(type_name):
    return (
        type_name == "Variant"
        or type_name in builtin_classes
        or type_name == "Nil"
        or type_name.startswith("typedarray::")
    )


def is_engine_class(type_name):
    global engine_classes
    return type_name == "Object" or type_name in engine_classes


def is_struct_type(type_name):
    # This is used to determine which keyword to use for forward declarations.
    global native_structures
    return is_included_struct_type(type_name) or type_name in native_structures


def is_refcounted(type_name):
    return type_name in engine_classes and engine_classes[type_name]


def is_included(type_name, current_type):
    """
    Check if a builtin type should be included.
    This removes Variant and POD types from inclusion, and the current type.
    """
    if type_name.startswith("typedarray::"):
        return True
    to_include = get_enum_class(type_name) if is_enum(type_name) else type_name
    if to_include == current_type or is_pod_type(to_include):
        return False
    if to_include == "GlobalConstants" or to_include == "UtilityFunctions":
        return True
    return is_engine_class(to_include) or is_variant(to_include)


def correct_default_value(value, type_name):
    value_map = {
        "null": "nullptr",
        '""': "String()",
        '&""': "StringName()",
        "[]": "Array()",
        "{}": "Dictionary()",
        "Transform2D(1, 0, 0, 1, 0, 0)": "Transform2D()",  # Default transform.
        "Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)": "Transform3D()",  # Default transform.
    }
    if value in value_map:
        return value_map[value]
    if value == "":
        return f"{type_name}()"
    if value.startswith("Array["):
        return f"{{}}"
    return value


def correct_typed_array(type_name):
    if type_name.startswith("typedarray::"):
        return type_name.replace("typedarray::", "TypedArray<") + ">"
    return type_name


def correct_type(type_name, meta=None):
    type_conversion = {"float": "double", "int": "int64_t", "Nil": "Variant"}
    if meta != None:
        if "int" in meta:
            return f"{meta}_t"
        elif meta in type_conversion:
            return type_conversion[type_name]
        else:
            return meta
    if type_name in type_conversion:
        return type_conversion[type_name]
    if type_name.startswith("typedarray::"):
        return type_name.replace("typedarray::", "TypedArray<") + ">"
    if is_enum(type_name):
        if is_bitfield(type_name):
            base_class = get_enum_class(type_name)
            if base_class == "GlobalConstants":
                return f"BitField<{get_enum_name(type_name)}>"
            return f"BitField<{base_class}::{get_enum_name(type_name)}>"
        else:
            base_class = get_enum_class(type_name)
            if base_class == "GlobalConstants":
                return f"{get_enum_name(type_name)}"
            return f"{base_class}::{get_enum_name(type_name)}"
    if is_refcounted(type_name):
        return f"Ref<{type_name}>"
    if type_name == "Object" or is_engine_class(type_name):
        return f"{type_name} *"
    if type_name.endswith("*"):
        return f"{type_name[:-1]} *"
    return type_name


def get_gdextension_type(type_name):
    type_conversion_map = {
        "bool": "int8_t",
        "uint8_t": "int64_t",
        "int8_t": "int64_t",
        "uint16_t": "int64_t",
        "int16_t": "int64_t",
        "uint32_t": "int64_t",
        "int32_t": "int64_t",
        "int": "int64_t",
        "float": "double",
    }

    if type_name.startswith("BitField<"):
        return "int64_t"

    if type_name in type_conversion_map:
        return type_conversion_map[type_name]
    return type_name


def escape_identifier(id):
    cpp_keywords_map = {
        "class": "_class",
        "char": "_char",
        "short": "_short",
        "bool": "_bool",
        "int": "_int",
        "default": "_default",
        "case": "_case",
        "switch": "_switch",
        "export": "_export",
        "template": "_template",
        "new": "new_",
        "operator": "_operator",
        "typeof": "type_of",
        "typename": "type_name",
        "enum": "_enum",
    }
    if id in cpp_keywords_map:
        return cpp_keywords_map[id]
    return id


def get_operator_id_name(op):
    op_id_map = {
        "==": "equal",
        "!=": "not_equal",
        "<": "less",
        "<=": "less_equal",
        ">": "greater",
        ">=": "greater_equal",
        "+": "add",
        "-": "subtract",
        "*": "multiply",
        "/": "divide",
        "unary-": "negate",
        "unary+": "positive",
        "%": "module",
        "**": "power",
        "<<": "shift_left",
        ">>": "shift_right",
        "&": "bit_and",
        "|": "bit_or",
        "^": "bit_xor",
        "~": "bit_negate",
        "and": "and",
        "or": "or",
        "xor": "xor",
        "not": "not",
        "and": "and",
        "in": "in",
    }
    return op_id_map[op]


def get_default_value_for_type(type_name):
    if type_name == "int":
        return "0"
    if type_name == "float":
        return "0.0"
    if type_name == "bool":
        return "false"
    if type_name.startswith("typedarray::"):
        return f"{correct_type(type_name)}()"
    if is_enum(type_name):
        return f"{correct_type(type_name)}(0)"
    if is_variant(type_name):
        return f"{type_name}()"
    if is_refcounted(type_name):
        return f"Ref<{type_name}>()"
    return "nullptr"


header = """\
/**************************************************************************/
/*  $filename                                                             */
/**************************************************************************/
/*                         This file is part of:                          */
/*                             GODOT ENGINE                               */
/*                        https://godotengine.org                         */
/**************************************************************************/
/* Copyright (c) 2014-present Godot Engine contributors (see AUTHORS.md). */
/* Copyright (c) 2007-2014 Juan Linietsky, Ariel Manzur.                  */
/*                                                                        */
/* Permission is hereby granted, free of charge, to any person obtaining  */
/* a copy of this software and associated documentation files (the        */
/* "Software"), to deal in the Software without restriction, including    */
/* without limitation the rights to use, copy, modify, merge, publish,    */
/* distribute, sublicense, and/or sell copies of the Software, and to     */
/* permit persons to whom the Software is furnished to do so, subject to  */
/* the following conditions:                                              */
/*                                                                        */
/* The above copyright notice and this permission notice shall be         */
/* included in all copies or substantial portions of the Software.        */
/*                                                                        */
/* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,        */
/* EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF     */
/* MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. */
/* IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY   */
/* CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,   */
/* TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE      */
/* SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.                 */
/**************************************************************************/
"""


def add_header(filename, lines):
    desired_length = len(header.split("\n")[0])
    pad_spaces = desired_length - 6 - len(filename)

    for num, line in enumerate(header.split("\n")):
        if num == 1:
            new_line = f"/*  {filename}{' ' * pad_spaces}*/"
            lines.append(new_line)
        else:
            lines.append(line)

    lines.append("// THIS FILE IS GENERATED. EDITS WILL BE LOST.")
    lines.append("")
