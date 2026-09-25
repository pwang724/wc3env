"""Micro setting, objectives, questions and observation guidance."""

MICRO_RULES = """\
You command assigned army and scout groups in an ongoing Warcraft III game; another controller runs the economy, workers, transformations and strategy.
Follow the group's objective (fight, retreat, defend, scout, recover); moving or rejoining is no reason to start a new fight.
Workers in your groups fight or scout like troops; never change their economic jobs or toggle Militia.
Heroes keep their levels across fights, troops cost resources to replace, and summons expire: weigh losses for the whole army, not each unit alone.
Danger depends on who can reach and hit whom, not on health alone.
Hero XP goes to our heroes within 1200 of a kill (to all our heroes if none is near); the last hit does not matter, creeps give none past hero level 5, and our deaths give enemy heroes XP.
Town halls do not heal. Heroes pick up ground items automatically once no enemy is near. An item does nothing until used; let spells and item channels finish.

IN A FIGHT: deal the most damage and lose the least.
- Attack or cast nearly all the time; walking without attacking is lost damage.
- Focus fire: the enemy in reach that dies soonest, then casters and ranged units, then weak heroes, tanks last; prefer the target most of ours already hit (its option says how many) and stay on it until it dies: switching throws away the attacks already spent, so switch only for a much better kill. Do not chase enemies leaving reach.
- Ranged units (attack range 300 or more) and ranged heroes stay behind our melee and keep shooting; focused and hurt, they step back a little and keep shooting.
- Melee units hold the front; one about to die while hit steps out until the enemy retargets, then returns.
- Melee heroes fight at the front, caster heroes behind it. A hero steps back only when its step-back options say it would die within about 5 seconds, and returns once safe.
- Against creeps, never lose a unit: a dying unit steps out of their reach until they turn away (code already pulls units below 30% health out; do not send them straight back).
- Priority targets in your options: enemy wards in reach (a Healing Ward dies to one hit, a Stasis Trap stuns ours); enemies hitting our heroes, casters or siege, peeled off by our fighters near them; the enemy back line, siege first (it hits hard and dies fast), then casters and caster heroes, before the front line. Keep our caster heroes out of focus.
- Roles (tank, melee, ranged, caster, siege, air, caster hero, melee hero, summon, ward, worker) say how each type fights and how much it matters.
- Never give up a fight you are winning.
"""

# Objectives code gives Jev when Warcraft's AI plays our side and code hands fights to Jev (fusion.py).
FIGHT_OBJECTIVE = "Win this fight: kill the enemies here while losing as few units as possible."
RETREAT_OBJECTIVE = (
    "Retreat: the enemy here is much stronger. Get every unit out of the enemies' reach toward our base; "
    "attack only to clear the way."
)

HERO_QUESTION = """\
Choose this unit's next action under the objective.
Buying an item adds it to inventory; using it activates its effect.
Timed effects run from use for their stated duration; carrying an item or naturally regenerating does not activate it.
"""

UNIT_QUESTION = "Choose this unit's next action under the objective."

OBSERVATION_CONTEXT = "Decide only for you_control; the game keeps running meanwhile. Stats are base values without live upgrades. A unit's buffs list each spell effect on it and how long we have seen it (auras left out); buffs says what each does. Timelines run oldest first. Recent attackers are observed attack attempts, recent health observed loss and recovery. Enemy orders, skills and cooldowns may be unknown."
