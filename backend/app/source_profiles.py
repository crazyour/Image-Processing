"""Vision-only source descriptions. These do not change the OpenAI plan schema."""
from pydantic import Field
from .schemas import Diagnosis, Strict
from .manufacturing_awareness import ProductStructureProfile

class StructureDiagnosis(Diagnosis):
    product_structure_profile: ProductStructureProfile | None = None

class Point(Strict):
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)

class ProductFingerprint(Strict):
    outer_shape: str
    orientation: str
    hole_layout: str; part_count: int = Field(ge=1)
    material: str
    color: str
    texture: str
    aspect_ratio: str
    key_geometry: str
    installation_direction: str

class ProductDiagnosis(StructureDiagnosis):
    product_fingerprint: ProductFingerprint; product_outline: list[Point] = Field(min_length=3, max_length=80)
    foreground_points: list[Point] = Field(min_length=3, max_length=20)
    background_points: list[Point] = Field(min_length=3, max_length=40)

class ReferenceProductDiagnosis(StructureDiagnosis):
    """Visible source facts for reference editing, without unused mask proposals."""
    product_fingerprint: ProductFingerprint

class IdentityProfile(Strict):
    head: str
    ears: str
    eyes: str
    muzzle: str
    markings: str
    chest: str
    body: str
    pose: str
    tail: str
    hair: str
    clothing: str
    relationship: str

class PhotoDiagnosis(StructureDiagnosis):
    identity_profile: IdentityProfile

def source_schema(context):
    if context.get("purpose") == "scene_product":
        if context.get("scene_direct_reference_edit"):
            return ReferenceProductDiagnosis
        return ProductDiagnosis
    elif not context.get("input"):
        context.get("input")
    if {}.get("module") == "PHOTO_TO_PRODUCT":
        return PhotoDiagnosis
    
    return StructureDiagnosis

def identity_instruction(profile):
    return "; ".join((f"{key}={value}" for key, value in profile.items()))
