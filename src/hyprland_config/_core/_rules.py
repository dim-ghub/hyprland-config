"""v3 windowrule / layerrule grammar constants.

The boolean-effect and boolean-matcher sets are intrinsic to the rule
grammar — Hyprland 0.53+ rejects bare boolean effects with "missing a value",
so every emitter (live-apply via ``hl.keyword``, on-disk serialization, the
v2→v3 migration) needs to auto-fill ``on``. Centralised here so the three
consumers share one source of truth.
"""

# v3 ``windowrule`` effects whose only argument is a boolean. Emitters
# auto-fill ``on`` when the args field is empty so Hyprland 0.53+ accepts
# the line.
V3_BOOL_EFFECTS: frozenset[str] = frozenset(
    {
        # Static
        "float", "tile", "fullscreen", "maximize", "center", "pseudo",
        "no_initial_focus", "pin",
        # Dynamic
        "persistent_size", "no_max_size", "stay_focused",
        "allows_input", "dim_around", "decorate", "focus_on_activate",
        "keep_aspect_ratio", "nearest_neighbor",
        "no_anim", "no_blur", "no_dim", "no_focus", "no_follow_mouse",
        "no_shadow", "no_shortcuts_inhibit", "no_screen_share", "no_vrr",
        "opaque", "force_rgbx", "sync_fullscreen", "immediate", "xray",
        "render_unfocused",
    }
)  # fmt: skip


# v3 ``windowrule`` matcher keys whose value is a boolean (``true``/``false``).
V3_BOOL_MATCHERS: frozenset[str] = frozenset(
    {"xwayland", "float", "fullscreen", "pin", "focus", "group", "modal"}
)


# v3 ``layerrule`` effects whose only argument is a boolean. Hyprland 0.54.3
# rejects bare bool layer effects with "missing a value", same as v3 windowrule
# effects.
LAYER_BOOL_EFFECTS: frozenset[str] = frozenset(
    {
        "no_anim",
        "blur",
        "blur_popups",
        "dim_around",
        "xray",
        "no_screen_share",
    }
)
