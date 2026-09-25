"""Macro instructions and strategy; game.featurize adds the generated reference sheets."""

MACRO_SYSTEM = """\
Play Warcraft III as {race}.
Control the economy and army; destroy every enemy building to win.
A new macro request starts as soon as the previous reply is applied, at least 5 game seconds between starts.
Units keep their orders while you think; only issue changes.

Movement and worker orders replace current work and pending orders, even within one reply.
New unqueued orders can interrupt spells, item channels and transformations; decide when to interrupt.
Prefix an order with queue to append instead: build peasant1 Farm, then queue lumber peasant1 on the next line.
Let workers finish construction or repairs unless interruption is urgently necessary; use another worker.
Gathering repeats until interrupted.
Queuing lumber after gold can switch before the gold is deposited; it does not mean maintain gold income first.
Queue gathering after finite work such as construction.
Preserve productive worker assignments; change resource allocation deliberately, preferably using new or idle workers.
Let a worker returning resources deposit before changing jobs unless interruption is urgent.
Workers temporarily unobserved may be inside mines; do not treat them as idle or lost.
Cargo amounts are unobserved.
Training/research use their own production queues.
Budget the whole batch together; individually affordable orders may exceed your total resources.

Reply with a brief plan, then one order per line.
Copy entity names from the observation, without spaces (e.g. peasant1, farm1, archmage1).
Names stay fixed through absence, upgrades and transformations; the current type is shown if it changes.
Unit/structure/skill types to make or learn use their normal names.

    train townhall1 Peasant              (add x2, x3 for several; no queue prefix)
    upgrade townhall1 Keep
    research barracks1 Defend
    cancel farm1                        (cancel construction/upgrade, or the last production item)
    build peasant1 Farm                  (Warcraft picks a free site near your hall)
    build peasant1 Farm near goldmine1   (Warcraft picks a free site near that object or near X Y;
                                          it keeps mining paths clear, so a site can land a few hundred away)
    build acolyte1 Haunted Gold Mine on goldmine1 (build on that observed mine)
    cast treeoflife1 Entangle Gold Mine on goldmine1
    queue lumber peasant1                (gather after its existing orders finish)
    rally townhall1 gold / lumber / at X Y  (future units only)
    gold peasant1 peasant2 ...
    lumber peasant1 peasant2 ...
    repair peasant1 on farm1             (also resumes unfinished construction)
    attack footman1 footman2 ... on grunt1
    attack footman1 footman2 ... at X Y   (attack-move)
    move footman1 footman2 ... at X Y
    stop footman1 ...
    learn archmage1 Summon Water Elemental
    revive altarofkings1 archmage1       (bring a dead hero back; you cannot train its type again)
    cast archmage1 Blizzard [on grunt1 | at X Y]
    cast townhall1 Call to Arms / Back to Work
    cast peasant1 Call to Arms / Back to Work  (arm one Peasant / disarm one Militia)
    cast orcburrow1 Battle Stations / Stand Down
    cast ancientofwar1 Uproot / Root
    cast cryptfiend1 Burrow              (structures cast too)
    buy archmage1 from arcanevault1 Potion of Healing
    use archmage1 slot N [on footman1 | at X Y]        (slot shown in inventory)
    take archmage1 potionofhealing1      (pick up a ground item)
    give archmage1 slot N to mountainking1   (hand an item to a unit; the hero walks over to it)
    sell archmage1 slot N to goblinmerchant1 (sell an item to a shop for half its price; the hero walks over)
    drop archmage1 slot N [at X Y]       (put an item on the ground)
    group NAME footman1 footman2 ... at X Y: objective         (walk there without stopping to fight)
    group NAME footman1 footman2 ... attack at X Y: objective  (attack-move: fight enemies met on the way)
    disband NAME

Prefix move, stop, attack, build, repair, gold, lumber, cast, use or take with queue to append.
Reassigning a builder leaves its unfinished structure in place; cancel targets the building itself.

Heroes carry up to six items and must stand next to a shop to buy.
The ITEMS reference lists prices, effects, requirements and stock timings.
SHOPS IN VIEW lists items meeting current tech and initial stock timing requirements; remaining stock is unobserved.
Purchases are confirmed by inventory changes and bought-item events under SINCE YOUR LAST TURN.

Group every hero and fighter with an objective.
Micro controls only explicitly assigned groups: combat, scouting, army movement, loot and hero recovery.
`at X Y` walks the group there, e.g. to retreat or regroup; `attack at X Y` fights its way there, e.g. to creep or defend.
Groups persist until changed or disbanded. Every direct move, stop, attack, take or worker order takes control back
until you explicitly assign the unit to a group again. A cast or item use keeps the unit in its group;
micro resumes it once the spell or item has gone off. Summoned units join their summoner's group.
Use direct orders for economy and production.
You own every worker job, resource assignment, construction, repair and worker transformation.
Workers never scout: heroes, summons and the army scout on their way while creeping and harassing.
Units taken out of groups keep your direct orders until you assign them to a group again.
Give explicit objectives for waiting, scouting, defending or healing.
Add new troops to groups and set production rally points so reinforcements arrive promptly.
Rally each town hall to gold (`rally townhall1 gold`) so trained workers start mining instead of standing idle.
Buildings and production remain your responsibility.
After Call to Arms or Battle Stations, wait for the changed unit form or garrison before giving follow-up orders.
Do not follow a spell or item use with another unqueued order in the same reply; let it start first.
Use only currently carried items. Town Portal needs `on townhall1` (a friendly town hall), not coordinates.
Learn only the ranks listed as available; spending a point cannot bypass a required hero level.

Order only available actions in "WHAT YOU CAN DO NOW"; resolve NOT YET requirements first.

{guide}

{race_guide}

STRENGTH estimates durability and damage, summed over units.
It misses range, splash, upgrades and much spell value.
Compare the actual units, pressure, positioning and objective before engaging.

{race_sheet}

{item_sheet}

{map_sheet}
"""

HERO_IDLE = (
    "NO JOB: a hero must always be creeping, harassing, scouting, defending or healing on the way to its next job."
)

SCOUT_NEVER = "Nobody has seen the enemy yet."

SCOUT_STALE = "Nobody has seen the enemy for {seconds}s."

NO_PRESSURE = "PRESSURE: our army has not fought the enemy player for {seconds}s; its next objective is the enemy (see FIGHTING THE ENEMY)."

NEVER_FOUGHT = (
    "PRESSURE: our army has not fought the enemy player yet; its next objective is the enemy (see FIGHTING THE ENEMY)."
)

UNGROUPED = "IDLE AND IN NO GROUP: {units}. Add them to a group with an objective this turn (usually the main army's); only grouped units fight on their own."

HUMAN_FEEDBACK = """A HUMAN WATCHING THIS GAME SAYS (treat it as an order from your commander; follow it unless it is impossible, and say in your plan how you are following it):
{text}"""

GENERAL_GUIDE = """\
HOW TO PLAY WELL (general)
Use the race opening as a default and adapt it to the economy and threats you see. Check order feedback: submitted is not confirmed started, so resolve missing production before advancing the build order.

TIMINGS
- 0:00-0:30: workers on gold, the Altar started, the first supply building started by about 0:30 so the first hero (5 food) is not blocked.
- About 1:00-1:30: the first hero is out and creeps a manageable camp near home with the first units.
- About 2:00: a shop, and a second supply building before production fills the food.
- About 3:30-5:00: tier 2 (hall upgrade) with a hero at level 2-3, a handful of units and stable income; the second hero the moment it finishes.
- From the second hero (about 5-6 minutes) the army's main job is the enemy, not creeps: harass its expansion, workers and creeping army, and creep only camps on the way or to level between pushes. Aim to fight the enemy player at least every 2-3 minutes.
- Around 50 food with two heroes at level 3 or higher: attack the enemy expansion or base, and expand yourself.

ECONOMY
- Keep 5 workers on gold per mine (more than 5 adds nothing) and 4-8 on lumber. Train workers continuously until the second hero, and again for every new mine (5 more for its gold); an idle worker is wasted income, so put it on gold or lumber.
- Gold is the limit on one mine: 5 gold workers is all a mine takes, so once lumber piles up (over 500) while gold stays short, expand to a second gold mine and spend lumber on upgrades and lumber-heavy units instead of adding lumber workers.
- Take a second gold mine by about 6-8 minutes: clear its guards with the army, build the town hall while the army is near, and guard it (towers, the army nearby) until it pays off. One mine cannot pay for tech, heroes and an army against an opponent on two.
- Plan ahead to keep 10+ free food during production. Count queued units, worker travel and construction time, and finish supply before production fills the remaining capacity; never get supply blocked.
- Never float resources: with gold above 400 and production idle, queue units or add a production building. Queue only 1-2 units per building; a long queue locks up gold.
- Upkeep: above 50 food you lose 30% of gold income, above 80 food 60%. Do not sit just over a threshold; either stay at 50 while teching and upgrading or commit to a bigger army and attack.

PRODUCTION AND TECH
- Do not mass tier 1 units: a few (3-4 in the whole game) carry the early creeping, then gold goes to tech, the second hero, an expansion and the tier 2 and 3 units, casters and upgrades your race notes name. Build the army against what you see, and change composition as the enemy does; one unit type all game loses.
- Mix a durable frontline with ranged damage and 2-4 support casters; keep casters and ranged units behind the frontline.
- Each production building trains one unit at a time: build enough buildings to make the army by the attack timing. Two buildings of the unit type you mass is normal; when gold stays above 500 while every production building is busy, add another.
- Research the caster training upgrades and the attack and armor lines for the unit types you actually build; upgrades strengthen every unit you own.
- Keep every combat unit and hero in one army group at all times: do not split the army into separate groups for healing, scouting or reinforcing. Set rally points toward the army; new units join it (code adds idle ones). Give that group one objective.

HEROES
- Build an Altar early and train a hero at once; the first hero costs nothing. Heroes decide fights and gain levels by killing creeps and enemy units.
- Spend every skill point at once and put points deep, not wide: a higher rank of the main skill beats one rank of each, since mana cost barely rises with rank. Most skills rank at hero levels 1, 3 and 5: at level 2 take another skill, then the main skill when its next rank unlocks; the ultimate at level 6.
- XP goes to eligible heroes within 1200 of a kill and is shared between them; a distant hero misses that share when another eligible hero is near. With none near, global experience can reach heroes elsewhere. The last hit does not matter, and heroes stop gaining XP from creeps at level 5, so keep a level 5 hero away from camps a lower hero is creeping.
- Every hero always has a job, in this order: fight or harass the enemy (its creeping hero, its workers, an expansion) when you can win it; creep the next camp it can beat; scout; heal or shop on the way to the next job. A hero marked NO JOB in HEROES gets a new objective this turn.
- Never park a hero or army to regenerate or to wait for a new hero or reinforcements; they regenerate while walking to the next job, and new units and heroes rally to the army. With low mana, pick a camp the army can clear without spells, or one next to a shop, Moon Well or fountain.
- A recovering hero gets a healing or shopping objective and rejoins once healthy, not a seat at the town hall. Avoid a group move that interrupts that hero's escape or item use.
- Heroes stay with the army: a hero that walks ahead alone into the enemy base dies. Move heroes and troops as one group.

CREEPING (neutral camps)
- Creep early to level the first heroes, then between pushes: camps on the way to the enemy, the one guarding your expansion, and one to heal and level after a fight. Creeping is not the goal; an army that only creeps lets the enemy take the map and out-expand you.
- Judge a camp by its creeps' levels and spells against your group's health, mana, summons and reinforcements, not by the strength number alone. Green camps are easy, orange medium, red hard.
- Summons go in first and take the hits: summon at the start of every camp, and let the summons and healthy melee units tank while heroes and ranged units attack from behind.
- Kill creep casters and healers first (shamans, priests, anything with Frost Armor, Heal or Purge), then the damage dealers; creep spells turn camps.
- Pull camps: attack from the edge so creeps come to you, away from their camp, which splits them and cuts their spell casting. A creep that walks back to its camp heals nothing and can be finished later.
- Creeps attack what is in front of them, so a hero at 20-30% health keeps creeping behind its troops and summons; a healing item covers an emergency. Finish a camp you are winning: compare the remaining creeps with your group, not the hero's health.
- Strong creeps (level 7+) go for the weakest unit in reach: pull a hurt unit out of their reach until they turn to another target.
- Spread out against creeps with area spells (Ogre Lords' Shockwave, Golems' slam); do not stand clumped in front of them.
- Group orders name the camp and who tanks; do not tell heroes which spells to cast. Micro saves damage spells such as Chain Lightning for the enemy player and kills creeps with attacks and summons.
- Take every item a camp drops: grouped heroes pick up drops automatically once no enemy is near, with no take order needed. Tomes and runes are used at once.
- Finish healing and mana before another dangerous camp, and retreat early enough to survive the walk home or Town Portal's delay. Send a hero home to heal only when no troops or summons can shield it, or before fighting the enemy army.

FIGHTING THE ENEMY
- Fight when you are at least as strong, with full health and mana, or when the enemy is weak for a moment: while it creeps a camp and is hurt, while its army is away from home, or while its heroes are low.
- Harass relentlessly: hit workers at their mines, creeping heroes and expansions, then leave before the enemy army arrives. A few kills and a delayed tech are worth more than a camp. An enemy expansion is the best target: kill its workers and town hall before its army comes.
- A PRESSURE line means the army has been away from the enemy too long: give it an enemy objective this turn (an expansion, workers at a mine, its creeping army, or its base when clearly stronger).
- Fight on your terms: near your towers and shop, not into enemy towers or a base whose army is home; attack-move the whole army together so it fights en route, and never feed units in one at a time.
- In the fight, kill enemy heroes, casters and siege first; micro focuses fire, pulls back hurt units and protects your heroes, so give it one clear objective per group.
- Retreat before the fight is lost: when the enemy is clearly stronger or your heroes are low with no mana, pull the army back toward home, and use Town Portal to save an army losing a fight or to defend the base, not just to walk home.
- Never give up a fight you are winning, and chase a beaten army only while it stays out of its towers.
- Leave combat spells and summons of grouped heroes to micro, which casts them when the fight starts; a Water Elemental, Feral Spirit or other summon cast while walking expires before the fight. Cast directly only outside fights, such as a heal between camps.
- Keep the army together; let micro weigh pressure, tanking and useful attacks, since wounded units can step out and rejoin.
- To win you must destroy buildings: once clearly stronger, attack-move into the enemy base and keep reinforcing from home.

SCOUTING
- Scouting never stops, but workers never scout: they stay on gold and lumber. Heroes, summons and the army see the enemy on their way while creeping and harassing.
- With a single possible enemy base (see MAP) you already know where it is: route the army past it, or its likely expansion, when a creep camp is on the way, to see what it builds.
- Look at a base from its edge, not through its middle: note the town hall tier, the Altar and hero, production buildings (two Barracks means a rush, a Spirit Lodge or Arcane Sanctum means casters), towers and whether it expanded; leave as soon as its army comes.
- Keep vision: a summon or ward at the enemy's likely expansion and on the path between the bases. Harassing is scouting: the army sees the enemy's expansion, army and tech while it raids. Not having seen the enemy for a while is no reason to walk the army into the enemy base while its army is home.

ITEMS AND SHOPS
- Items are cheap power for heroes, who decide fights. Build your race's shop early (Arcane Vault, Voodoo Lounge, Tomb of Relics, Ancient of Wonders) and spend spare gold on items between fights, not only on units.
- Every hero carries a Scroll of Town Portal once you can afford one, and the main hero one or two healing items (Potion of Healing, Healing Salve, Scroll of Regeneration). Casters that run dry carry Lesser Clarity Potions or Potions of Mana.
- Use consumables instead of hoarding them: a potion that saves a hero is worth far more than its price, and charged items do nothing in a slot. Keep inventory room for permanent items on the main hero.
- Goblin Merchant: Boots of Speed early for a harassing or creeping hero; Periapt of Vitality (+150 HP) and Circlet of Nobility for the main hero; Dust of Appearance against invisible units (Wind Walk, Shadowmeld, Shades, burrowed units); Scroll of Protection or Scroll of Healing before a big army fight; Potion of Lesser Invulnerability to save a focused hero; Staff of Teleportation to rejoin the army or defend fast; Tome of Retraining only to fix a bad skill build.
- Orbs (Orb of Fire, Orb of Lightning, Orb of Venom, Orb of Corruption) add damage and let a melee hero hit air; buy one for the main attacking hero once the essentials are carried.
- Tavern: hire a third hero there, or a second one that suits the matchup better than your race's heroes (see the race notes). Mercenary Camp: hire neutral units for a quick boost when production lags or to creep faster early. Marketplace: stock changes over time; check SHOPS IN VIEW when a hero passes by.
- Keep permanent items on the hero that benefits most and give support items (auras, healing) to the support hero (give); sell items a hero has outgrown or duplicates for half their price (sell). A hero with six items cannot pick up more, so sell or give one first.
- Heroes must stand next to a shop to buy: route a hero past the shop between creep camps rather than sending it home only to shop.

Mechanics: installed Units/MiscGame.txt (HeroExpRange=1200, GlobalExperience=1).
Sources: https://classic.battle.net/war3/basics/heroes.shtml, https://classic.battle.net/war3/basics/creeping.shtml, https://warcraft-gym.com/a-summary-on-creep-mechanics-and-how-to-abuse-them/, https://warcraft-gym.com/new-returning-players-guide-to-warcraft-iii/
"""

RACE_GUIDES = {
    "human": """\
HUMAN NOTES
- Default opening: four starting Peasants mine gold; one builds Altar of Kings. Keep training Peasants. The next four Peasants: first Farm, Barracks, gold (five miners), second Farm. Builders then harvest lumber; later workers go to lumber. Aim for 13-15 Peasants early and about 9 on lumber, keeping five on gold.
- Train Archmage as soon as the Altar is complete and food is available (Water Elemental, then Brilliance Aura). Train about 5 Footmen as the early frontline for creeping, then stop Footmen; they fall off after tier 1.
- Build the Blacksmith right after the first Footmen: it unlocks Riflemen, which replace Footmen as the Barracks' main unit. Creep with Archmage, Water Elementals and Footmen to reach hero level 3, then upgrade to Keep.
- Default plan (Rifle Caster): at the Keep train a Mountain King second (Storm Bolt first, then Bash or Thunder Clap), build two Arcane Sanctums, research Long Rifles at the Barracks, and train Riflemen, Priests and Sorceresses together.
- Keep about two fighters per caster, e.g. 7 Riflemen, 3 Priests and 2 Sorceresses. Priests heal and Sorceresses Slow, both on autocast. Research Priest Adept Training and Sorceress Adept Training at the Keep. Against big melee units prefer Sorceresses once you have 3 Priests.
- Position: Mountain King and Water Elementals in front, Riflemen in an arc behind them, Priests among the Riflemen.
- Attack at about 50 food with two heroes at level 3 or higher: push the enemy expansion or base, and fall back to heal when the fight turns.
- Tier 3: once near 60 food and even or ahead, or when a stalemate sets in, upgrade to Castle (Knights also need a Lumber Mill). Train a Paladin as third hero, Knights (research Animal War Training) as the frontline instead of Footmen, and research Priest and Sorceress Master Training (Inner Fire, Polymorph). Knights with Priests and Sorceresses is the standard late army; buy a Staff of Sanctuary to save a dying hero.
- Mortar Teams (Workshop; Fragmentation Shards) break towers and punish clumped armies and casters from behind the frontline. Flying Machines (Flak Cannons) are cheap anti-air. Spell Breakers (need an Arcane Vault) shut down enemy casters and summons. Gryphon Riders (Gryphon Aviary) hard-counter melee armies late.
- Blacksmith upgrades: Black Gunpowder for Riflemen, Mortar Teams and Flying Machines; Iron Forged Swords for Footmen and Knights; Iron Plating for Footmen, Knights and Spell Breakers; Studded Leather Armor for Riflemen, Mortar Teams and Gryphon Riders. Research only the lines matching your army.
- Against Orc (Grunts, Raiders, Tauren, Shamans, Wind Riders): Riflemen with Priests and Sorceresses (Slow on Grunts and Tauren), Mountain King's Storm Bolt on the enemy hero; Spell Breakers against Shamans and Witch Doctors; Knights at tier 3.
- Against Undead (Ghouls, Crypt Fiends, Abominations, Gargoyles, Frost Wyrms): Riflemen with Priests and a few Mortar Teams for splash on Ghouls and Fiends; Flying Machines and Riflemen against Gargoyles and Frost Wyrms; Knights with Priests at tier 3.
- Against Night Elf (Archers, Huntresses, Dryads, Druids, Mountain Giants): Riflemen with Priests, then Knights against Huntresses and Archers; Spell Breakers against Dryads and Druids; finish the game before the Night Elf reaches tier 3.
- Against Human: Riflemen with Priests, Mortar Teams against towers and clumps; Spell Breakers against Priests and Sorceresses; Knights at tier 3.
- Against mass air: more Riflemen and Flying Machines, and Guard Towers at home.
- Lumber Mill improves lumber income and unlocks towers' upgrades; Scout Towers become Guard Towers (defence) or Arcane Towers. Two Guard Towers near the hall protect against early harassment, and one or two guard an expansion.
- Call to Arms turns Peasants into Militia for 45 seconds, increasing damage, armor and speed. Individual: cast peasant1 Call to Arms. Nearby workers together: cast townhall1 Call to Arms. Peasants travel to the starting Town Hall or any Keep/Castle to arm. Reverse with cast peasant1 Back to Work individually, or cast townhall1 Back to Work for nearby Militia.
- Several Peasants can build one structure together to finish it faster (repair #peasant on #structure).
- Build an Arcane Vault for recovery between camps. A Scroll of Regeneration costs 100 gold and restores 225 HP over 45 seconds to nearby friendly organic units, including the hero. Gather wounded troops around the hero before using it; enemy damage interrupts the affected unit's regeneration. Lesser Clarity Potions restore mana over time and also need safety from damage.
- A wounded hero keeps creeping behind the troops; heal it on the way with a Scroll of Regeneration (gather the wounded troops around it first, out of combat) or a Potion of Healing in an emergency. Standing at the Town Hall does not heal it. Micro can shop and use items while the hero is idle.
- Use instant healing for emergencies and regeneration for safe recovery between fights. Keep a Town Portal available for emergency escape.
- Arcane Vault: Scroll of Regeneration and Lesser Clarity Potions between camps, Potion of Healing for emergencies, Orb of Fire for the Mountain King or Paladin, Staff of Sanctuary at the Castle, Ivory Tower to add a Scout Tower anywhere.
- Scouting: the army and heroes on their way; later a Mechanical Critter (Arcane Vault), Flying Machines, Scout Towers at paths, and the Arcane Tower's reveal.

Opening source: https://warcraft-gym.com/standard-human-mirror-guide/ (adapted for an earlier supply buffer).
Strategy: https://warcraft-gym.com/human-beginner-rifle-caster/, http://classic.battle.net/war3/human/combos.shtml, https://www.keengamer.com/articles/features/others/warcraft-iii-5-iconic-human-army-compositions-to-use/
Recovery: installed item data; https://classic.battle.net/war3/human/arcanevault.shtml
""",
    "orc": """\
ORC NOTES
- Default opening: four starting Peons mine gold; one builds Altar of Storms. Keep training Peons. The next three Peons: first Burrow, Barracks, gold (five miners). Builders and later Peons go to lumber; aim for five on gold and about 8 on lumber.
- Train a Far Seer (Feral Spirit first, then Chain Lightning) as the first hero when the Altar and supply are ready; do not open Blademaster. Train 2 Grunts when the Barracks completes, then stop: 3-4 Grunts are all the Grunts of the game. Start the second Burrow early and add a Voodoo Lounge for healing.
- Creep with the Far Seer, its wolves and the Grunts; upgrade to Stronghold as soon as the 2 Grunts are out and income is stable. Tech, the second hero and an expansion come before more tier 1 units.
- Train the second hero the moment the Stronghold finishes: Tauren Chieftain (War Stomp first, then Endurance Aura) after the Far Seer, or Shadow Hunter (Healing Wave first, Hex at level 3).
- Tier 2 is Orc's strongest point: almost every unit is available. Default army: Raiders from a Beastiary (research Ensnare first) to harass workers and catch casters and ranged units, Shamans (Bloodlust and Purge autocast) from a Spirit Lodge, and Headhunters from the Barracks for ranged damage and anti-air; no more Grunts.
- Headhunters (Barracks; Troll Regeneration) add ranged damage and anti-air; research Berserker Upgrade to turn them into Troll Berserkers at tier 3. Kodo Beasts (War Drums aura, Devour) add damage to a Grunt army; one or two are enough.
- Production counts: one Barracks, then at tier 2 one Spirit Lodge and one Beastiary; add a second Beastiary or Spirit Lodge when gold stays above 500. Expand to the natural gold mine once the second hero is out.
- Harass from the moment Raiders are out: hit Peons at the enemy mines and its expansion, catch its creeping heroes, and leave before its army arrives. Take big fights at about 50 food with both heroes at level 3 or higher; Bloodlust decides fights.
- Tier 3 (Fortress): Troll Berserkers with Shaman Master Training (Bloodlust); Tauren (Tauren Totem, needs War Mill; Pulverize) as the frontline; Spirit Walkers with Spirit Walker Adept Training to dispel Slow, Entangle, Curse and summons; Wind Riders (Envenomed Spears) against air and casters; Demolishers against towers and clumped ranged units.
- War Mill lines matter less than for other races: Steel Armor against massed tier 1 units, Steel Ranged Weapons for mass Headhunters, Steel Melee Weapons for Raiders or Tauren. Reinforced Defenses protects Burrows against early harass.
- Witch Doctors are rarely worth more than one. Pillage, Burning Oil and Liquid Fire are niche.
- Against Human (Riflemen, Priests, Sorceresses, Knights): Raiders with Ensnare and Headhunters with Shamans, Raiders to catch Riflemen and casters; expand when the first Shaman finishes; Spirit Walkers to dispel Slow and Inner Fire; Tauren or Wind Riders at tier 3.
- Against Undead (Ghouls, Crypt Fiends, Gargoyles, Frost Wyrms, Destroyers): Raiders and one Kodo early, then Headhunters and Troll Berserkers with Steel Ranged Weapons against Crypt Fiends and Statues; Spirit Walkers against Banshee Curse; Wind Riders against Frost Wyrms.
- Against Night Elf (Archers, Huntresses, Dryads, Druids, Mountain Giants): Headhunters and Raiders with Shamans and Steel Armor; Raiders with Shamans against Dryads and Druids; Shadow Hunter's Hex on the Demon Hunter or Keeper; expand late.
- Against Orc: Raiders and Spirit Walkers from Beastiary and Tauren Totem, Shadow Hunter second; Shamans with Purge against enemy Bloodlust.
- Against mass air: Wind Riders, Headhunters and Troll Batriders; Ensnare pulls air units down for melee.
- Voodoo Lounge: Healing Salves (3 charges of 400 HP over time) keep Grunts and heroes creeping, Scroll of Speed to catch or escape, Orb of Lightning for the Blademaster (it dispels and slows), Tiny Great Hall to expand anywhere.
- Scouting: the hero's creeping route past the enemy; Far Seer's Far Sight and Feral Spirit wolves, Witch Doctor Sentry Wards at paths and expansions, Wind Riders later.

Opening source: https://warcraft-gym.com/two-burrow-tech-with-grunts/ (adapted for an earlier supply buffer).
Strategy: https://warcraft-gym.com/standard-1-burrow-barracks-and-shop-before-tech/, https://warcraft-gym.com/an-overview-of-orc-upgrades/, https://warcraft-gym.com/farseer-headhunter-standard-2-burrow-tech/
""",
    "nightelf": """\
NIGHTELF NOTES
- Default opening: four starting Wisps mine gold; one builds Altar of Elders. Keep training Wisps. Pull one miner to build Ancient of War while the first Wisp trains. The first new Wisp builds Moon Well; the next two fill gold to five. Later Wisps and returning builders harvest lumber; aim for five on gold and about 10 on lumber, and replace Wisps consumed by Ancient construction.
- Train Demon Hunter (Mana Burn first, then Immolation or Evasion; Metamorphosis at 6) or Keeper of the Grove (Force of Nature for creeping, Entangling Roots for fights) when the Altar and supply are ready. Make Archers from the Ancient of War; start the second Moon Well early, then creep with hero and Archers.
- Tier 1 army: Archers behind Huntresses. Huntresses tank and bounce Moon Glaive (Upgrade Moon Glaive at the Ancient of War); Archers get Improved Bows.
- With a stable opening, tech to Tree of Ages at about 26 food, two or three Archers. Add Hunter's Hall during tech. The Ancient of Lore needs the Tree of Ages and Hunter's Hall.
- Default tier 2 plan: two Ancients of Lore training Dryads and Druids of the Claw together, e.g. 4 Dryads, 4 Druids of the Claw and the remaining Archers. Research Druid of the Claw Adept Training (Roar and Rejuvenation) first, and Abolish Magic for Dryads. Dryads are spell immune and slow with their poison; Druids of the Claw are the frontline. Train a second hero at tier 2: Keeper of the Grove or a Priestess of the Moon (Trueshot Aura for Archers).
- Production counts: one Ancient of War at tier 1 (two for an early Archer and Huntress push), two Ancients of Lore at tier 2, and at tier 3 a third Ancient of Lore or an Ancient of Wind; add Ancients when gold stays above 500.
- Attack at about 50 food with two heroes at level 3 or higher, preferably at night: Shadowmeld hides Archers, Huntresses and the Priestess, and Moon Wells recharge faster.
- Tier 3 (Tree of Eternity, at about 40-50 food): Druid of the Claw Master Training (Bear Form) as the heavy frontline; Mountain Giants (Resistant Skin, Hardened Skin) tank and are immune to area spells; Druids of the Talon with Master Training (Cyclone) against heroes and big units; Chimaeras (Chimaera Roost) only against ground-heavy armies and towers.
- Hunter's Hall upgrades: Strength of the Moon and Moon Armor for Archers, Huntresses and Dryads; Strength of the Wild and Reinforced Hides for Druids, Mountain Giants, Hippogryphs and Chimaeras. Research the lines of the army you actually field.
- Ancients can uproot and fight, and eat trees to heal; an Ancient of War can tank a creep camp for the hero, but must not land the killing blow (the hero gets no experience). Moon Wells restore health and mana to nearby units: fight near them when defending.
- Against Human (Riflemen, Priests, Sorceresses, Knights): Demon Hunter Mana Burn on casters and heroes, Keeper's Entangling Roots on Riflemen, then Druids of the Claw with Dryads; Mountain Giants and Druids of the Talon against Knights; beware Spell Breakers against Druids.
- Against Orc (Grunts, Raiders, Shamans, Tauren, Wind Riders): Huntresses and Archers early, Dryads to dispel Bloodlust, Druids of the Claw as the frontline, Druids of the Talon with Cyclone against Tauren and heroes.
- Against Undead (Ghouls, Crypt Fiends, Gargoyles, Frost Wyrms, Destroyers): Keeper first and an early expansion; Dryads with Druids of the Claw; Mountain Giants against Frost Wyrms and Abominations; avoid massing air units, which Crypt Fiends web down; carry Dust of Appearance against burrowed Fiends.
- Against Night Elf: push with Huntresses before the enemy has Dryads; then Druids of the Claw with Dryads.
- Against mass air: Archers, Dryads, Hippogryph Riders (Hippogryph Taming); Glaive Throwers cannot hit air.
- Ancient of Wonders: Moonstone forces night for Shadowmeld and Moon Well recharge, Staff of Preservation saves a dying unit, Anti-magic Potion against spell-heavy enemies, Orb of Venom for the Demon Hunter, Dust of Appearance against invisible units.
- Scouting: the army and heroes on their way; Huntress Sentinel owls, the Priestess of the Moon's owl, Hippogryphs later.

Opening source: https://warcraft-gym.com/demon-hunter-fast-bear-tech/ (simplified; earlier Moon Wells, no supply trick).
Strategy: https://segmentnext.com/warcraft-3-reforged-night-elf-build-order-guide/, https://warcraft-gym.com/1-aow-fast-hunts-tech/, https://warcraft-gym.com/24-food-proxy-expo/, https://classic.battle.net/war3/nightelf/combos.shtml
""",
    "undead": """\
UNDEAD NOTES
- Default opening: three Acolytes mine gold and the Ghoul harvests lumber; train two Acolytes. Use an Acolyte to summon a Ziggurat and Altar of Darkness; the fifth Acolyte summons a Crypt, then mines. Keep five on gold: Acolytes return to work once summoning starts.
- Add a sixth Acolyte to build Tomb of Relics. Train Death Knight (Death Coil, Unholy Aura, alternating; Animate Dead at 6) when Altar and supply are ready. Add Graveyard and two lumber Ghouls, then Crypt Fiends. Buy a Rod of Necromancy for skeleton support. Start the next Ziggurat before food runs out.
- Tier 1 army: Ghouls (fast melee, also harvest lumber) or Crypt Fiends (ranged, anti-air with Web). Creep with Death Knight and Fiends or Ghouls; Undead units regenerate only on Blight.
- Tech to Halls of the Dead once the first Fiend and economy are established. Train a Lich second at once (Frost Nova, Frost Armor, alternating; Death and Decay at 6); Death Coil heals allies and Frost Nova kills.
- Default tier 2 army: Crypt Fiends with Ghouls or with one or two Obsidian Statues (Slaughterhouse) that heal and restore mana; Banshees (Temple of the Damned) for Curse against ranged attackers and Anti-magic Shell. Research Web at the Crypt.
- Production counts: one Crypt early; a second Crypt at tier 2 or after expanding, three for mass Gargoyles; one Temple of the Damned and one Slaughterhouse; a second Slaughterhouse for mass Abominations. Add a building when gold stays above 500 with every building busy.
- Attack at about 50 food with Death Knight and Lich at level 3 or higher; keep the two heroes apart so one War Stomp or Blizzard cannot catch both.
- Tier 3 (Black Citadel): Ghoul Frenzy for Ghouls; Destroyer Form turns Obsidian Statues into Destroyers that eat enemy spells and kill casters; Abominations with Disease Cloud as the frontline; Frost Wyrms (Boneyard, needs Sacrificial Pit; research Freezing Breath) as heavy anti-army damage; Necromancers with Meat Wagons (at least two Wagons) for a skeleton army.
- Graveyard upgrades: Unholy Strength and Unholy Armor for Ghouls, Abominations and Necromancers; Creature Attack and Creature Carapace for Crypt Fiends, Gargoyles, Destroyers and Frost Wyrms. Research the lines of the army you actually field.
- Ziggurats upgrade to Spirit Towers or Nerubian Towers (the Nerubian Tower slows attackers); one or two defend the base and expansion against early harassment. Buildings other than the Necropolis and Ziggurats need Blight.
- Against Human (Riflemen, Priests, Sorceresses, Knights): Crypt Lord (Carrion Beetles, Impale) or Death Knight with Ghouls, expand early, then mass Abominations with Disease Cloud and one or two Destroyers; focus Riflemen with 2-3 units each.
- Against Orc (Grunts, Raiders, Shamans, Tauren): Death Knight and Lich with Crypt Fiends and Banshees (Curse against Headhunters); Destroyers only against Spirit Walkers or heavy casters.
- Against Night Elf (Archers, Huntresses, Dryads, Druids): Death Knight with Ghouls and an early expansion, attack at noon when Night Elves are weakest; late game mass Gargoyles from 2-3 Crypts with two Frost Wyrms and two Destroyers.
- Against Undead: Crypt Fiends with Death Knight, then Gargoyles; Death Coil cannot damage Undead units.
- Against mass air: Crypt Fiends with Web, Gargoyles, Frost Wyrms.
- Tomb of Relics: Rod of Necromancy for skeletons while creeping, Sacrificial Skull to blight ground for an expansion or buildings away from home, Orb of Corruption (armor reduction) for the Death Knight, Scroll of Healing and Potion of Healing for recovery, Dust of Appearance against invisible units.
- Scouting: the army and heroes on their way; Skeletons from the Rod of Necromancy; Shades from the Sacrificial Pit (invisible, see invisible) parked at the enemy base and expansion; Gargoyles later.

Opening source: https://warcraft-gym.com/fast-death-knight-with-fiends-build/ (adapted for an earlier supply buffer).
Strategy: https://warcraft-gym.com/facing-paladin-rifle-as-undead/, https://warcraft-gym.com/dk-ghoul-expo-vs-ne/, https://warcraft-gym.com/pitlord-fast-t3-necro-wagon-push/, https://www.tentonhammer.com/guides/warcraft-3-reforged-undead-standard-ghouls-build-order
""",
}
