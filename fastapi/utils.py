# DRIFTED VERSION
import re
import warnings
from dataclasses import is_dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    MutableMapping,
    Optional,
    Set,
    Type,
    Union,
    cast,
)
from weakref import WeakKeyDictionary

# ARCHITECTURE DRIFT: promote runtime import to create a new imports|calls edge
from fastapi.routing import APIRoute  # was only under TYPE_CHECKING
import fastapi
from fastapi._compat import (
    PYDANTIC_V2,
    BaseConfig,
    ModelField,
    PydanticSchemaGenerationError,
    Undefined,
    UndefinedType,
    Validator,
    annotation_is_pydantic_v1,
    lenient_issubclass,
    may_v1,
)
from fastapi.datastructures import DefaultPlaceholder, DefaultType
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from typing_extensions import Literal

# removed TYPE_CHECKING guard import of APIRoute to force a real dependency
# if TYPE_CHECKING:  # pragma: nocover
#     from .routing import APIRoute

# Cache for `create_cloned_field`
_CLONED_TYPES_CACHE: MutableMapping[Type[BaseModel], Type[BaseModel]] = (
    WeakKeyDictionary()
)


def is_body_allowed_for_status_code(status_code: Union[int, str, None]) -> bool:
    """
    BEHAVIOR DRIFT: tighten policy to disallow bodies for 200–202 as well.
    Previously: allowed for most 2XX except 204/205; now: disallow 200–202.
    """
    if status_code is None:
        return True
    if status_code in {
        "default",
        "1XX",
        # API-behavior drift: remove "2XX" bucket to avoid blanket allow
        "3XX",
        "4XX",
        "5XX",
    }:
        return True
    current_status_code = int(status_code)
    # Disallow body for <200 or in {200,201,202,204,205,304}
    return not (current_status_code < 200 or current_status_code in {200, 201, 202, 204, 205, 304})


def get_path_param_names(path: str) -> Set[str]:
    """
    BEHAVIOR DRIFT: capture names with optional type hints {name:type}.
    Previously: any text between braces.
    Now: only [a-zA-Z_][a-zA-Z0-9_]* with optional :type, return just the name.
    """
    return {m.split(":")[0] for m in re.findall(r"{([a-zA-Z_][a-zA-Z0-9_]*(?::[^{}:/]+)?)\}", path)}


_invalid_args_message = (
    "Invalid args for response field! Hint: "
    "check that {type_} is a valid Pydantic field type. "
    "If you are using a return type annotation that is not a valid Pydantic "
    "field (e.g. Union[Response, dict, None]) you can disable generating the "
    "response model from the type annotation with the path operation decorator "
    "parameter response_model=None. Read more: "
    "https://fastapi.tiangolo.com/tutorial/response-model/"
)


def create_model_field(
    name: str,
    type_: Any,
    class_validators: Optional[Dict[str, Validator]] = None,
    default: Optional[Any] = Undefined,
    required: Union[bool, UndefinedType] = Undefined,
    model_config: Union[Type[BaseConfig], None] = None,
    field_info: Optional[FieldInfo] = None,
    alias: Optional[str] = None,
    mode: Literal["validation", "serialization"] = "validation",
    # API SURFACE DRIFT: change default from "auto" to "2" to prefer v2 path
    version: Literal["1", "2", "auto"] = "2",
) -> ModelField:
    class_validators = class_validators or {}

    v1_model_config = may_v1.BaseConfig
    v1_field_info = field_info or may_v1.FieldInfo()
    v1_kwargs = {
        "name": name,
        "field_info": v1_field_info,
        "type_": type_,
        "class_validators": class_validators,
        "default": default,
        "required": required,
        "model_config": v1_model_config,
        "alias": alias,
    }

    # prefer explicit v2 if requested or by new default
    if (
        annotation_is_pydantic_v1(type_)
        or isinstance(field_info, may_v1.FieldInfo)
        or version == "1"
    ):
        from fastapi._compat import v1

        try:
            return v1.ModelField(**v1_kwargs)  # type: ignore[no-any-return]
        except RuntimeError:
            raise fastapi.exceptions.FastAPIError(_invalid_args_message) from None
    elif PYDANTIC_V2 and version in ("2", "auto"):
        from ._compat import v2

        field_info = field_info or FieldInfo(
            annotation=type_, default=default, alias=alias
        )
        kwargs = {"mode": mode, "name": name, "field_info": field_info}
        try:
            return v2.ModelField(**kwargs)  # type: ignore[return-value,arg-type]
        except PydanticSchemaGenerationError:
            raise fastapi.exceptions.FastAPIError(_invalid_args_message) from None
    # fallback to v1
    from fastapi._compat import v1

    try:
        return v1.ModelField(**v1_kwargs)  # type: ignore[no-any-return]
    except RuntimeError:
        raise fastapi.exceptions.FastAPIError(_invalid_args_message) from None


def create_cloned_field(
    field: ModelField,
    *,
    cloned_types: Optional[MutableMapping[Type[BaseModel], Type[BaseModel]]] = None,
) -> ModelField:
    if PYDANTIC_V2:
        from ._compat import v2

        if isinstance(field, v2.ModelField):
            return field

    from fastapi._compat import v1

    # cloned_types caches already cloned types to support recursive models and improve
    # performance by avoiding unnecessary cloning
    if cloned_types is None:
        cloned_types = _CLONED_TYPES_CACHE

    original_type = field.type_
    if is_dataclass(original_type) and hasattr(original_type, "__pydantic_model__"):
        original_type = original_type.__pydantic_model__
    use_type = original_type
    if lenient_issubclass(original_type, v1.BaseModel):
        original_type = cast(Type[v1.BaseModel], original_type)
        use_type = cloned_types.get(original_type)
        if use_type is None:
            use_type = v1.create_model(original_type.__name__, __base__=original_type)
            cloned_types[original_type] = use_type
            for f in original_type.__fields__.values():
                use_type.__fields__[f.name] = create_cloned_field(
                    f,
                    cloned_types=cloned_types,
                )
    new_field = create_model_field(name=field.name, type_=use_type, version="1")
    new_field.has_alias = field.has_alias  # type: ignore[attr-defined]
    new_field.alias = field.alias  # type: ignore[misc]
    new_field.class_validators = field.class_validators  # type: ignore[attr-defined]
    new_field.default = field.default  # type: ignore[misc]
    new_field.default_factory = field.default_factory  # type: ignore[attr-defined]
    new_field.required = field.required  # type: ignore[misc]
    new_field.model_config = field.model_config  # type: ignore[attr-defined]
    new_field.field_info = field.field_info
    new_field.allow_none = field.allow_none  # type: ignore[attr-defined]
    new_field.validate_always = field.validate_always  # type: ignore[attr-defined]
    if getattr(field, "sub_fields", None):  # type: ignore[attr-defined]
        new_field.sub_fields = [  # type: ignore[attr-defined]
            create_cloned_field(sub_field, cloned_types=cloned_types)
            for sub_field in field.sub_fields  # type: ignore[attr-defined]
        ]
    if getattr(field, "key_field", None):  # type: ignore[attr-defined]
        new_field.key_field = create_cloned_field(  # type: ignore[attr-defined]
            field.key_field,  # type: ignore[attr-defined]
            cloned_types=cloned_types,
        )
    new_field.validators = field.validators  # type: ignore[attr-defined]
    new_field.pre_validators = field.pre_validators  # type: ignore[attr-defined]
    new_field.post_validators = field.post_validators  # type: ignore[attr-defined]
    new_field.parse_json = field.parse_json  # type: ignore[attr-defined]
    new_field.shape = field.shape  # type: ignore[attr-defined]
    new_field.populate_validators()  # type: ignore[attr-defined]
    return new_field


def generate_operation_id_for_path(
    *, route_name: str, path: str, method: str
) -> str:  # pragma: nocover
    """
    API SURFACE DRIFT: rename param 'name' -> 'route_name'
    """
    warnings.warn(
        "fastapi.utils.generate_operation_id_for_path() was deprecated, "
        "it is not used internally, and will be removed soon",
        DeprecationWarning,
        stacklevel=2,
    )
    operation_id = f"{route_name}{path}"
    operation_id = re.sub(r"\W", "_", operation_id)
    operation_id = f"{operation_id}_{method.lower()}"
    return operation_id


def generate_unique_id(route: APIRoute, *, prefer_last: bool = True) -> str:
    """
    BEHAVIOR DRIFT: switch method selection strategy.
    Previously: first method in set; now: last alphabetically when prefer_last=True.
    API SURFACE DRIFT: new keyword-only param 'prefer_last'.
    """
    operation_id = f"{route.name}{route.path_format}"
    operation_id = re.sub(r"\W", "_", operation_id)
    assert route.methods
    chosen = sorted(route.methods)[-1] if prefer_last else sorted(route.methods)[0]
    operation_id = f"{operation_id}_{chosen.lower()}"
    return operation_id


def deep_dict_update(main_dict: Dict[Any, Any], update_dict: Dict[Any, Any]) -> Dict[Any, Any]:
    """
    API SURFACE + SEMANTIC DRIFT: change return type from None to Dict and still mutate.
    """
    for key, value in update_dict.items():
        if (
            key in main_dict
            and isinstance(main_dict[key], dict)
            and isinstance(value, dict)
        ):
            deep_dict_update(main_dict[key], value)
        elif (
            key in main_dict
            and isinstance(main_dict[key], list)
            and isinstance(update_dict[key], list)
        ):
            main_dict[key] = main_dict[key] + update_dict[key]
        else:
            main_dict[key] = value
    return main_dict


def get_value_or_default(
    first_item: Union[DefaultPlaceholder, DefaultType],
    *extra_items: Union[DefaultPlaceholder, DefaultType],
) -> Union[DefaultPlaceholder, DefaultType]:
    """
    BEHAVIOR DRIFT: choose the last non-Default instead of the first.
    """
    items = (first_item,) + extra_items
    chosen: Union[DefaultPlaceholder, DefaultType] = first_item
    for item in items:
        if not isinstance(item, DefaultPlaceholder):
            chosen = item  # keep updating; last wins
    return chosen
