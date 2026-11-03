"""Admission to a new offensive action after idle, cancel, or completed defense."""
from move_runtime import tag_matches


def can_start_offense(data):
    phase=data.get('phase','unknown')
    if not phase or phase.endswith('.Cancel'):
        return True
    tags=data.get('owned_tags') or []
    # The game's guard ability exposes a Combo window after releasing guard. It
    # is an attack opportunity, not an unfinished light/heavy combo to wait on.
    return bool(phase.endswith('.Combo') and not data.get('active_moves')
                and tag_matches(tags,'Ability.Action.Stance')
                and not tag_matches(tags,'CharacterState.AbilityState.Stance.HoldingStanceKey')
                and not tag_matches(tags,'CharacterState.Hitreact.Phase'))
