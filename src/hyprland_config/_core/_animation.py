"""Animation tree + parse/serialize for ``animation =`` and ``bezier =`` keywords.

The animation tree (which animations exist, parent/style relationships) is
static knowledge of Hyprland's grammar — analogous to the option catalog in
``hyprland_schema``, but tied to the *config language* rather than the
versioned option list. It lives here so emitters and parsers can share one
source of truth without pulling ``hyprland-schema`` (which has its own
version-keyed migration story).
"""

from dataclasses import dataclass

# Curves Hyprland accepts without a ``bezier =`` definition.
HYPRLAND_NATIVE_CURVES: frozenset[str] = frozenset({"default", "linear"})


# ---------------------------------------------------------------------------
# Animation tree: (name, styles, children).
# Children inherit parent values when not explicitly overridden.
# ---------------------------------------------------------------------------

ANIMATION_TREE = (
    (
        "windows",
        ("slide", "popin", "gnomed"),
        (
            ("windowsIn", (), ()),
            ("windowsOut", (), ()),
            ("windowsMove", (), ()),
        ),
    ),
    (
        "layers",
        ("slide", "popin", "fade"),
        (
            ("layersIn", (), ()),
            ("layersOut", (), ()),
        ),
    ),
    (
        "fade",
        (),
        (
            ("fadeIn", (), ()),
            ("fadeOut", (), ()),
            ("fadeSwitch", (), ()),
            ("fadeShadow", (), ()),
            ("fadeDim", (), ()),
            (
                "fadeLayers",
                (),
                (
                    ("fadeLayersIn", (), ()),
                    ("fadeLayersOut", (), ()),
                ),
            ),
            (
                "fadePopups",
                (),
                (
                    ("fadePopupsIn", (), ()),
                    ("fadePopupsOut", (), ()),
                ),
            ),
            ("fadeDpms", (), ()),
        ),
    ),
    ("border", (), ()),
    ("borderangle", ("once", "loop"), ()),
    (
        "workspaces",
        ("slide", "slidevert", "fade", "slidefade", "slidefadevert"),
        (
            ("workspacesIn", (), ()),
            ("workspacesOut", (), ()),
            (
                "specialWorkspace",
                (),
                (
                    ("specialWorkspaceIn", (), ()),
                    ("specialWorkspaceOut", (), ()),
                ),
            ),
        ),
    ),
    ("zoomFactor", (), ()),
    ("monitorAdded", (), ()),
)


_TreeNode = tuple[str, tuple[str, ...], tuple["_TreeNode", ...]]
_FlatEntry = tuple[str, str | None, int, tuple[str, ...]]


def _flatten_tree(
    tree: tuple[_TreeNode, ...], parent: str | None = None, depth: int = 0
) -> tuple[_FlatEntry, ...]:
    """Flatten the animation tree into an ordered tuple of (name, parent, depth, styles)."""
    result: list[_FlatEntry] = []
    for name, styles, children in tree:
        result.append((name, parent, depth, styles))
        result.extend(_flatten_tree(children, parent=name, depth=depth + 1))
    return tuple(result)


# Flat tree: ``((name, parent_name, depth, own_styles), ...)``. The synthetic
# ``global`` root carries no styles itself; it exists so child animations have
# a parent to inherit from.
ANIM_FLAT: tuple[_FlatEntry, ...] = (("global", None, 0, ()),) + _flatten_tree(
    ANIMATION_TREE, parent="global", depth=1
)

# Lookup: ``name -> (parent, depth, own_styles)``.
ANIM_LOOKUP: dict[str, tuple[str | None, int, tuple[str, ...]]] = {
    name: (parent, depth, styles) for name, parent, depth, styles in ANIM_FLAT
}


def _build_children(flat: tuple[_FlatEntry, ...]) -> dict[str, tuple[str, ...]]:
    children: dict[str, list[str]] = {}
    for name, parent, _, _ in flat:
        if parent is not None:
            children.setdefault(parent, []).append(name)
    return {parent: tuple(names) for parent, names in children.items()}


ANIM_CHILDREN: dict[str, tuple[str, ...]] = _build_children(ANIM_FLAT)


def get_styles_for(name: str) -> tuple[str, ...]:
    """Return the styles for *name*, inheriting from the parent chain."""
    parent, _, styles = ANIM_LOOKUP[name]
    if styles:
        return styles
    while parent and parent in ANIM_LOOKUP:
        _, _, pstyles = ANIM_LOOKUP[parent]
        if pstyles:
            return pstyles
        parent = ANIM_LOOKUP[parent][0]
    return ()


# ---------------------------------------------------------------------------
# AnimationData — typed shape for one ``animation =`` keyword line
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnimationData:
    """One ``animation = NAME,onoff,speed,curve[,style]`` keyword line.

    Mirrors :class:`BindData` — typed Hyprlang grammar that's also what the
    Lua emitter consumes. ``style`` is optional in the wire format; an empty
    string here serialises as a missing trailing comma.
    """

    name: str
    enabled: bool = True
    speed: float = 0.0
    curve: str = ""
    style: str = ""

    def body(self) -> str:
        """Value half: ``NAME,onoff,speed,curve[,style]`` (no keyword prefix)."""
        val = f"{self.name},{int(self.enabled)},{self.speed},{self.curve}"
        if self.style:
            val += f",{self.style}"
        return val

    def to_line(self) -> str:
        """Full config line: ``animation = NAME,onoff,speed,curve[,style]``."""
        return f"animation = {self.body()}"

    @classmethod
    def from_parts(cls, name: str, parts: list[str]) -> "AnimationData":
        """Parse pre-split keyword fields into an :class:`AnimationData`.

        Expected layout: ``[name, onoff, speed, curve, style?]``. Missing
        trailing fields fall back to Hyprland's defaults (the ``style``
        field is optional in the wire format).
        """
        return cls(
            name=name,
            enabled=parts[1] != "0" if len(parts) > 1 else True,
            speed=float(parts[2]) if len(parts) > 2 else 0.0,
            curve=parts[3] if len(parts) > 3 else "default",
            style=parts[4] if len(parts) > 4 else "",
        )

    @classmethod
    def from_body(cls, body: str) -> "AnimationData":
        """Parse an ``animation =`` value half into an :class:`AnimationData`."""
        parts = [p.strip() for p in body.split(",")]
        if not parts or not parts[0]:
            raise ValueError(f"empty animation body: {body!r}")
        return cls.from_parts(parts[0], parts)


# ---------------------------------------------------------------------------
# BezierData — typed shape for one ``bezier =`` keyword line
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BezierData:
    """One ``bezier = NAME,x0,y0,x1,y1`` keyword line."""

    name: str
    points: tuple[float, float, float, float]

    def body(self) -> str:
        """Value half: ``NAME,x0,y0,x1,y1`` (no keyword prefix)."""
        x0, y0, x1, y1 = self.points
        return f"{self.name},{x0},{y0},{x1},{y1}"

    def to_line(self) -> str:
        """Full config line: ``bezier = NAME,x0,y0,x1,y1``."""
        return f"bezier = {self.body()}"

    @classmethod
    def from_body(cls, body: str) -> "BezierData":
        """Parse a ``bezier =`` value half into a :class:`BezierData`.

        Expected: ``NAME,x0,y0,x1,y1`` (exactly five comma-separated fields).
        Raises ``ValueError`` if the shape is wrong or the points aren't
        numeric.
        """
        parts = [p.strip() for p in body.split(",")]
        if len(parts) != 5:
            raise ValueError(f"bezier expects 5 fields, got {len(parts)}: {body!r}")
        try:
            points = (float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
        except ValueError as exc:
            raise ValueError(f"bezier control points must be numeric: {body!r}") from exc
        return cls(name=parts[0], points=points)
