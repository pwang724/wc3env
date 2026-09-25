"""What each unit type should do, added to that unit's question: one rule per line.

Heroes and casters get several lines each: when to cast, on whom, what not to spend mana on, and where to stand.
Sources for heroes are in game/data/hero_builds.json.
"""

# Added to the question of every unit with a spell it can cast.
CASTER_RULE = """\
Spells are what heroes and casters add: cast whenever a spell is ready and a good target is in range.
Aim spells where they do the most: several enemies at once, a key target to finish or disable, or a dying ally.
Check a target's buffs first: never repeat an effect it already has; dispel strong enemy buffs (Bloodlust, Inner Fire, Anti-magic Shell) and enemy debuffs on ours (Slow, Curse, Faerie Fire).
Summon only when the fight is about to start, so summons take hits instead of expiring on the way.
While creeping, kill creeps with attacks and summons; keep damage spells and mana for the enemy player, casting them on creeps only to save one of our units about to die.
"""

UNIT_PROMPTS = {
    # Archmage
    "Hamg": """\
Stay behind your melee line and summon a Water Elemental as soon as the fight starts, summon another whenever one dies or is about to expire, and send them onto enemy ranged units and casters.
Brilliance Aura is passive, so keep the Archmage close to your own heroes and casters so they regain mana faster.
Cast Blizzard only on clumped enemy ranged units or casters that are standing still, and stop channeling it if enemy melee units reach him.
He is fragile, so keep him behind the melee line.
""",
    # Mountain King
    "Hmkg": """\
Fight at the front with your melee units and open every fight with Storm Bolt on the enemy hero with the lowest health (or one that is channeling a spell), then have your whole army focus that hero.
Cast Thunder Clap when 3 or more enemy units are around the Mountain King to damage and slow them, and to stop enemies from running away.
Cast Avatar at the start of a big fight or when enemies focus him, then charge the enemy hero, since spells cannot hurt him while it lasts.
Always keep 75 mana for a Storm Bolt to finish a hero that tries to flee.
""",
    # Paladin
    "Hpal": """\
Stay just behind the front line and cast Holy Light on the most valuable wounded ally (usually a hero below about 60% health), or on an enemy undead unit to damage it, and keep 65 mana in reserve for an emergency heal.
Cast Divine Shield when the Paladin drops below about 35% health or is being focused, then walk to safety or keep healing while invulnerable.
Devotion Aura is passive, so stay close to your army so every unit gets the extra armor.
Cast Resurrection right after a big fight when 5 or more of your units have died nearby.
""",
    # Blood Mage
    "Hblm": """\
Stay at the back and cast Flame Strike on clumped enemy ranged units or casters that are standing still, or on a stunned hero, since it takes over a second to land.
Cast Banish on an enemy melee hero or melee unit attacking your casters so it cannot attack, or Banish a target and then hit it with Flame Strike for extra damage.
Use Siphon Mana on enemy caster heroes to drain their mana, or on your own hero to give it mana.
Summon the Phoenix at the start of a big fight.
""",
    # Blademaster
    "Obla": """\
Open with Wind Walk, walk past the enemy front line and backstab an enemy caster or wounded hero, and save 75 mana to Wind Walk away when below about 30% health.
Critical Strike is passive, so keep attacking one low-health target, ideally an enemy hero or caster, instead of switching targets.
Cast Mirror Image to dodge an incoming spell like Storm Bolt or Frost Nova, or to confuse the enemy about which Blademaster is real.
Cast Bladestorm when many enemy units are close around him, and do not stay in melee at low health without mana for Wind Walk.
""",
    # Far Seer
    "Ofar": """\
Cast Feral Spirit as soon as the fight starts and recast it whenever the wolves die or expire, sending the wolves onto enemy ranged units, casters and heroes.
Creeping: cast Feral Spirit at the start of every creep camp and recast it when the wolves expire; the wolves go in first and take the creeps' hits instead of our Grunts.
Save Chain Lightning for fights against the enemy player: cast it when 3 or more enemy units are close together, preferably ranged units and casters, or on a wounded enemy hero, and keep 120 mana ready to finish off a fleeing hero.
Against creeps, use Feral Spirit and attacks instead; cast Chain Lightning on creeps only to save one of our units about to die.
Stay behind your melee units, since the Far Seer is fragile.
Use Earthquake on enemy towers and buildings, not on moving armies.
""",
    # Tauren Chieftain
    "Otch": """\
Before War Stomp, walk into the middle of the enemy units so they clump around the Tauren Chieftain, and cast it only when its option shows 3 or more enemies within its area; never cast it on one or two.
The stunned units are then free kills for your army.
Endurance Aura is passive, so stay with your army to give them faster attacks and movement.
Cast Shockwave in a straight line through clumped enemy ranged units and casters.
Reincarnation brings him back once, so he can fight at the very front.
""",
    # Shadow Hunter
    "Oshd": """\
Stay behind your melee line and cast Healing Wave on a wounded ally hero or unit that stands among other wounded allies, and keep 90 mana ready for it.
Cast Hex on the most dangerous enemy unit or hero, such as a caster about to cast, a Mountain Giant, a Knight or a hero trying to escape.
Place Serpent Wards next to the enemy army to add damage and absorb hits.
Cast Big Bad Voodoo when enemies are about to kill several of your units in a big fight.
""",
    # Death Knight
    "Udea": """\
Death Coil heals our heroes first (it is offered on them alone while one is below half health), then our most valuable hurt units, such as Abominations, Frost Wyrms and Crypt Fiends; it also finishes a low-health enemy living hero. Keep 75 mana ready for it.
Unholy Aura is passive, so stay in the middle of your army to give it faster movement and health regeneration.
The Death Knight is fast: raid the enemy back line (casters, caster heroes and siege), which you are offered wherever they stand; leave tanks and air units to others.
Cast Animate Dead after many units have died on both sides to raise invulnerable corpses, and use Death Pact on a spare unit to heal the Death Knight in an emergency.
""",
    # Lich
    "Ulic": """\
Frost Nova is the Lich's damage: cast it every time it is ready, on a wounded enemy it would kill, on an enemy hero, or on a unit among clumped enemies.
Keep mana for Frost Nova: when mana runs short, cast Dark Ritual on one of our summons or a unit of ours about to die anyway, such as a skeleton, to turn it into mana.
Frost Armor autocast spends that mana, so turn it off and cast it by hand only on a hero of ours being hit by melee units.
Stay behind your army, since the Lich is fragile and slow.
Cast Death and Decay on a big enemy army fighting in one place.
""",
    # Dreadlord
    "Udre": """\
Stay in the middle of your army so Vampiric Aura heals your melee units as they attack.
Cast Carrion Swarm in a line through clumped enemy ranged units and casters to hit many at once.
Cast Sleep on an enemy hero or strong unit to take it out of the fight while your army kills the others.
Cast Inferno on the middle of the enemy army at the start of a big fight to stun them and add an Infernal.
""",
    # Crypt Lord
    "Ucrl": """\
Fight at the front with your melee units and cast Impale in a line through clumped enemy units and heroes to stun and damage them all.
Spiked Carapace is passive, so let enemy melee units hit the Crypt Lord and take damage back.
Summon Carrion Beetles before the fight for extra melee fighters and blockers.
Cast Locust Swarm in the middle of a big fight when many enemies are nearby.
""",
    # Demon Hunter
    "Edem": """\
Raid the enemy back line: siege units, casters and weak or wounded heroes, which you are offered wherever they stand.
Mana Burn enemy heroes and casters that have mana, before they can cast.
Evasion is passive and lets the Demon Hunter survive the dive; leave tanky units to our melee units.
Turn Immolation on among groups of weak enemy units or summons, and off when mana is low.
Cast Metamorphosis in a big fight or when low on health.
""",
    # Keeper of the Grove
    "Ekee": """\
Cast Entangling Roots on the enemy hero to hold it in place while your army focuses it, or on a strong enemy melee unit hitting your army.
Cast Force of Nature on nearby trees at the start of the fight to summon Treants that tank and block.
Thorns Aura is passive and helps your melee units.
Stay behind your army, cast Tranquility when many of your units are hurt and the enemy is not attacking.
""",
    # Priestess of the Moon
    "Emoo": """\
Stay behind your melee line and keep Searing Arrows on to add damage to her attacks, focusing enemy heroes and casters.
Trueshot Aura is passive, so keep her close to your ranged units such as Archers, Huntresses and Dryads.
Use Scout to find enemy units hidden in the fog before a fight.
Cast Starfall only when many enemies are nearby and your melee units protect her.
""",
    # Warden
    "Ewar": """\
Blink into the enemy back line to reach casters and ranged units, and Blink away to escape when the Warden is hurt.
Cast Fan of Knives when 3 or more enemy units are around the Warden, especially weak units like casters or summons.
Cast Shadow Strike on an enemy hero to slow and poison it while your army chases.
Cast Vengeance in a big fight where many units have died to summon spirits.
""",
    # Alchemist
    "Nalc": """\
Cast Healing Spray on a group of your wounded units fighting together, and keep mana to repeat it.
Cast Chemical Rage at the start of the fight so the Alchemist attacks and moves faster, and fight at the front with your melee units.
Cast Acid Bomb on the enemy hero or a strong unit to lower its armor so your army kills it faster.
Use Transmute on a large enemy creep or unit to kill it for gold.
""",
    # Dark Ranger
    "Nbrn": """\
Stay behind your melee line and keep Black Arrow on for enemy units, so each enemy killed by it raises a Dark Minion that fights for you.
Cast Silence on groups of enemy casters and caster heroes before they cast.
Use Life Drain on an enemy unit or hero to heal the Dark Ranger when she is hurt, but stop if enemies are attacking her.
Cast Charm on the strongest enemy non-hero unit (such as a Mountain Giant, Knight, Frost Wyrm or Tauren) to take control of it.
""",
    # Beastmaster
    "Nbst": """\
Summon the Bear, Quilbeast and Hawk before the fight so they fight with your army, and resummon them when they die.
Send the Bear into the enemy melee units, keep the Quilbeast attacking with your army, and use the Hawk to scout and chase enemy casters.
Fight at the front with your melee units, since the Beastmaster is tanky.
Cast Stampede on a large clumped enemy army or on retreating enemies, and stay out of harm while it channels.
""",
    # Firelord
    "Nfir": """\
Summon Lava Spawn as soon as the fight starts and resummon when they die, sending them into enemy melee units so they multiply.
Cast Soul Burn on the enemy caster hero or strongest enemy caster to silence and damage it.
Incinerate is passive and makes his attacks hurt nearby enemies.
Cast Volcano on enemy buildings or on a slow, clumped enemy army, and stay behind your army since he is fragile.
""",
    # Naga Sea Witch
    "Nngs": """\
Cast Forked Lightning on a group of close enemy units in front of the Naga to hit up to 3 of them at once.
Keep Frost Arrows on to slow enemy heroes and melee units that chase or flee.
Turn Mana Shield on when the Naga starts taking damage, and turn it off out of combat to save mana.
Cast Tornado on enemy armies or buildings in big fights, and stay behind your melee line.
""",
    # Pandaren Brewmaster
    "Npbm": """\
Fight at the front with your melee units and cast Breath of Fire in a cone at clumped enemy units, best right after Drunken Haze so they catch fire.
Cast Drunken Haze on the enemy hero or a strong enemy melee unit so it misses more often and moves slower.
Drunken Brawler is passive and lets the Brewmaster dodge attacks and land critical hits.
Cast Storm, Earth, and Fire at the start of a big fight to split into three strong fighters.
""",
    # Pit Lord
    "Nplh": """\
Fight in the middle with your melee units and cast Rain of Fire on clumped enemy ranged units and casters.
Cleaving Attack is passive, so attack groups of enemy melee units to hit them all.
Cast Howl of Terror when many enemy units are around to lower their damage.
Cast Doom on the strongest enemy hero or unit to silence it and gain a Doom Guard if it dies.
""",
    # Tinker
    "Ntin": """\
Build a Pocket Factory close to the enemy army at the start of the fight so it keeps producing goblins that fight and block.
Cast Cluster Rockets on clumped enemy ranged units or casters to damage and stun them.
Engineering Upgrade is passive and improves his other spells.
Turn into a Robo-Goblin in big fights to become a tanky melee fighter, and stay behind your army while in normal form.
""",
    # Priest
    "hmpr": """\
Stay behind your melee units.
Inner Fire comes first: whenever it is ready, cast it; it is offered on our heroes first, then tanks and high-damage units, then everyone else that lacks it.
Spend the remaining mana on Heal for our hurt heroes and key units.
Dispel Magic clears an area: aim it where enemy summons stand (it kills them) or on enemies carrying Bloodlust, Unholy Frenzy or Anti-magic Shell. Avoid spots where our units carry Inner Fire, since it strips our buffs too.
""",
    # Sorceress
    "hsor": """\
Stay behind your melee units.
Slow stays on autocast. Cast it by hand on the enemy melee unit or hero dealing the most damage whose buffs do not show Slow.
Cast Polymorph on the strongest enemy non-hero unit (Tauren, Knight, Mountain Giant, Abomination, Frost Wyrm) that is not already polymorphed; it cannot target heroes.
Do not cast Invisibility in a fight.
""",
    # Spell Breaker
    "hspt": """\
Fight at the front with your melee units and let Feedback burn mana from enemy casters you attack.
Use Spell Steal to take enemy buffs like Bloodlust or Unholy Frenzy onto your own units, and use Control Magic to take over enemy summons like Water Elementals or Treants.
""",
    # Shaman
    "oshm": """\
Stand behind your melee units, close enough to reach them with spells.
Bloodlust stays on autocast. Cast it by hand on a Tauren, Grunt, Raider or melee hero in the fight whose buffs do not show Bloodlust; never on one that already has it.
Purge is for enemy summons: Feral Spirit wolves, Water Elementals, Treants, skeletons and Spirit Wolves die to it outright. Purge the summon with the most hit points left, and skip one another of our casters is already casting on.
With no enemy summon in view, keep enough mana for the next Purge instead of casting it on ordinary units; the one exception is a fleeing enemy hero, which Purge slows.
Cast Lightning Shield on an enemy melee unit standing among several of their units, never on a unit standing alone.
""",
    # Troll Witch Doctor
    "odoc": """\
Stand behind your melee units; Witch Doctors are fragile.
Open every fight with a Stasis Trap where the enemy melee units are about to walk, just in front of our melee line. It stuns every enemy near it when it springs, so cast another once it has fired.
Cast a Healing Ward only once our melee units are fighting, in the middle of our hurt units. One ward per spot: if one of our Healing Wards (your_side, role ward) already stands within about 500 of that spot, do not cast another.
A Healing Ward costs about twice a Stasis Trap, so after one Healing Ward keep mana for the next Stasis Trap.
Place Sentry Wards only outside fights.
""",
    # Spirit Walker
    "ospm": """\
Stay behind your army and cast Spirit Link on your frontline units so damage is shared.
Cast Disenchant where several enemy summons such as Feral Spirit wolves stand together, since it destroys them all, and also to remove enemy buffs; switch to Ethereal form to avoid physical attacks, and use Ancestral Spirit to revive a fallen Tauren.
""",
    # Necromancer
    "unec": """\
Stay behind your army.
Raise Dead stays on autocast, so corpses become skeletons fighting for us.
Cast Unholy Frenzy on our heroes first, caster heroes like the Lich included, then our highest-damage attackers, melee or ranged (Abominations, Frost Wyrms, Crypt Fiends), those fighting now whose buffs do not show it.
Skip low-damage units such as Banshees, Necromancers and skeletons, and units below half health, since it drains health.
Cast Cripple on the enemy's strongest melee unit or hero whose buffs do not show Cripple.
""",
    # Banshee
    "uban": """\
Stay behind your army; a Banshee's attack is weak, so its spells are what it adds.
Curse enemy heroes and the enemy attackers dealing the most damage, those whose buffs do not show Curse.
Cast Anti-magic Shell on our heroes, first on one enemy casters are targeting; it blocks spells and keeps enemy dispels off it.
Use Possession only when the fight is safe for the Banshee, on the strongest enemy non-hero unit.
""",
    # Obsidian Statue
    "uobs": """\
Stay behind your army and keep Essence of Blight and Spirit Touch on autocast to restore your units' health and mana.
Morph into a Destroyer when you need a unit that drains enemy mana and removes buffs.
""",
    # Druid of the Talon
    "edot": """\
Stay behind your melee units.
Faerie Fire is your main job: it is cheap and strips armor, so cast it by hand every time it is ready, on an enemy our army is attacking whose buffs do not show Faerie Fire, until every enemy in the fight has it.
Cast Cyclone only on the one most dangerous enemy unit, such as a tank shielding their back line or a caster about to cast, and not when that leaves no mana for Faerie Fire.
Switch to Storm Crow form to escape or chase, and back to fight and cast.
""",
    # Dryad
    "edry": """\
Stay behind your melee units; Dryads are immune to spells, so let them face enemy casters.
Abolish Magic stays on autocast. Cast it by hand on enemy summons first (it kills them), then on enemies carrying Bloodlust, Inner Fire or Unholy Frenzy, then on our units carrying Slow, Curse or Faerie Fire.
Shoot enemy ranged units and casters, or an enemy melee unit chasing our Archers, which Slow Poison slows down.
Leave tanky melee units such as Mountain Giants and Druids of the Claw to our melee units; shooting them wastes your damage.
""",
    # Druid of the Claw (Night Elf Form)
    "edoc": """\
In this form a Druid of the Claw is a fragile melee unit (430 hit points against 810 as a bear), so switch to Bear Form as soon as enemy units come into view, before the armies meet.
Cast Roar first if it is ready and our army's buffs do not show it yet, then switch.
Rejuvenation is for our heroes: cast it on a hurt hero of ours, and on other units only when no hero of ours is hurt, then switch back to Bear Form.
""",
    # Druid of the Claw (Bear Form)
    "edcm": """\
Fight at the front as a tank with our melee units, and hold the line in front of our Archers, Dryads and casters.
Cast Roar when our army is fighting and its buffs do not show Roar.
Rejuvenation saves our heroes: while you have the mana, choose "Rejuvenation on <hero> (leaves Bear Form first)" for a hurt hero of ours, then return to Bear Form; do not spend it on other units while a hero of ours is hurt.
""",
    # Druid of the Talon (Storm Crow Form)
    "edtm": """\
Storm Crow Form is for flying: to escape, chase, or cross to where you are needed.
Switch back to Night Elf Form to cast Faerie Fire and Cyclone once you are behind our melee units.
""",
    # Faerie Dragon
    "efdr": """\
Send Faerie Dragons after enemy casters and summons, and turn Mana Flare on when near enemy casters so their spells hurt them.
Use Phase Shift to dodge attacks.
""",
    # Kodo Beast
    "okod": """\
Stay behind your army so the War Drums aura boosts nearby units' damage.
Use Devour on the strongest enemy melee unit to swallow it and take it out of the fight.
""",
    # Gargoyle
    "ugar": """\
Attack enemy air units first and ranged units second.
Use Stone Form to heal and survive when enemy anti-air focuses you.
""",
    # Crypt Fiend
    "ucry": """\
Stay behind your melee units and focus enemy air units and casters.
Use Web to bring enemy air units to the ground so your melee can hit them.
Do not Burrow in a fight, since a burrowed Crypt Fiend cannot attack; Burrow only to hide and heal away from the fight.
""",
    # Huntress
    "esen": """\
Pick off weak enemy units: wounded units, casters, ranged units and summons; Huntresses hit light and unarmored units hard.
Leave tanky units to our melee units.
""",
    # Archer
    "earc": """\
Stay behind your melee units and focus enemy casters, caster heroes, ranged units and air units in your attack options.
Leave tanky melee units such as Mountain Giants and bears to our melee units; shooting them wastes your damage.
""",
    # Rifleman
    "hrif": """\
Stay behind your melee units and focus enemy air units, casters and ranged units.
""",
    # Troll Headhunter
    "ohun": """\
Stay behind your melee units and shoot; do not walk into the enemy.
Otherwise focus enemy air units, casters and ranged units, and leave tanky melee units such as Tauren to our melee.
""",
    # Troll Berserker: the Headhunter after the Berserker Upgrade
    "otbk": """\
Stay behind your melee units and shoot; do not walk into the enemy.
Otherwise focus enemy air units, casters and ranged units, and leave tanky melee units such as Tauren to our melee.
Cast Berserk when enemies are in reach and this Troll Berserker is not being focused, since it takes extra damage while Berserk lasts.
""",
    # Flying Machine
    "hgyr": """\
Attack enemy air units, using Flak Cannons against groups of air units.
Stay near your army to spot invisible enemy units.
""",
    # Gryphon Rider
    "hgry": """\
Attack enemy ground units, especially the enemy hero or caster with the lowest health.
""",
    # Dragonhawk Rider
    "hdhw": """\
Cast Aerial Shackles on the strongest enemy air unit to take it out of the fight, and use Cloud on enemy towers.
Focus enemy casters and anti-air units.
""",
    # Wind Rider
    "owyv": """\
Attack enemy ground units, especially casters and heroes, since Envenomed Spears poison them.
""",
    # Troll Batrider
    "otbr": """\
Use Unstable Concoction to suicide into large groups of enemy air units like Gargoyles or Chimaeras.
Otherwise attack enemy buildings with Liquid Fire to burn them and stop repairs.
""",
    # Frost Wyrm
    "ufro": """\
Attack from behind your melee units, focusing enemy heroes and casters, and use Freezing Breath on enemy buildings.
""",
    # Chimaera
    "echm": """\
Attack enemy buildings and slow, clumped ground units with Corrosive Breath.
""",
    # Hippogryph
    "ehip": """\
A Hippogryph attacks only air units: when no enemy air unit is in the fight, cast Pick up Archer to become a Hippogryph Rider that shoots both air and ground units.
""",
    # Hippogryph Rider
    "ehpr": """\
A flying ranged unit that shoots air and ground: stay behind our melee units and pick off weak enemy units, casters and air units.
""",
    # Mortar Team
    "hmtm": """\
Stay far behind your melee units and fire at the enemy's back line: casters, ranged units and caster heroes behind their melee units, where your splash hits several.
Do not waste shots on enemy melee units at the front, and fire at buildings when no army is near.
Siege is precious like a hero: when its step-back options say it would die within about 6 seconds, step back behind our army until it is out of reach, then keep firing.
""",
    # Demolisher
    "ocat": """\
Stay behind your army and attack the enemy's back line: casters, ranged units and caster heroes standing behind their melee units, which your siege attack hits hard.
Do not waste shots on enemy melee units at the front, and attack enemy buildings and towers when no army is near.
Siege is precious like a hero: when its step-back options say it would die within about 6 seconds, step back behind our army until it is out of reach, then keep firing.
""",
    # Meat Wagon
    "umtw": """\
Stay behind your army and attack the enemy's back line: casters, ranged units and caster heroes behind their melee units.
Do not waste shots on enemy melee units at the front, attack buildings when no army is near, and carry corpses for your Necromancers.
Siege is precious like a hero: when its step-back options say it would die within about 6 seconds, step back behind our army until it is out of reach, then keep firing.
""",
    # Glaive Thrower
    "ebal": """\
Stay behind your army and attack the enemy's back line: casters, ranged units and caster heroes behind their melee units.
Do not waste shots on enemy melee units at the front, and attack buildings when no army is near.
Siege is precious like a hero: when its step-back options say it would die within about 6 seconds, step back behind our army until it is out of reach, then keep firing.
""",
    # Knight
    "hkni": """\
Fight at the front, focus enemy ranged units, casters and heroes, and use your speed to chase fleeing enemies.
""",
    # Grunt
    "ogru": """\
Fight at the front, hold the line in front of your casters, and focus enemy heroes and ranged units.
""",
    # Tauren
    "otau": """\
Fight at the front and hold the line in front of your casters; Pulverize splash hurts clumped enemy melee units, so fight where they bunch up.
""",
    # Spirit Wolf: Feral Spirit
    "osw1": """\
Raid the enemy back line: go around the fight to enemy siege units (Demolishers, Catapults, Mortar Teams, Meat Wagons, Glaive Throwers) first, then enemy casters and caster heroes; you are offered them wherever they stand.
One or two raiders per target are enough: when its option shows 2 or more of ours already attacking it, pick another back-line target or fight where you are.
Wolves are summons that expire, so spend them on the raid rather than on our front line.
Against creeps, go into the camp first and take the hits, so our Grunts and heroes lose less health.
""",
    # Dire Wolf: Feral Spirit
    "osw2": """\
Raid the enemy back line: go around the fight to enemy siege units (Demolishers, Catapults, Mortar Teams, Meat Wagons, Glaive Throwers) first, then enemy casters and caster heroes; you are offered them wherever they stand.
One or two raiders per target are enough: when its option shows 2 or more of ours already attacking it, pick another back-line target or fight where you are.
Wolves are summons that expire, so spend them on the raid rather than on our front line.
Against creeps, go into the camp first and take the hits, so our Grunts and heroes lose less health.
""",
    # Shadow Wolf: Feral Spirit
    "osw3": """\
Raid the enemy back line: go around the fight to enemy siege units (Demolishers, Catapults, Mortar Teams, Meat Wagons, Glaive Throwers) first, then enemy casters and caster heroes; you are offered them wherever they stand.
One or two raiders per target are enough: when its option shows 2 or more of ours already attacking it, pick another back-line target or fight where you are.
Wolves are summons that expire, so spend them on the raid rather than on our front line.
Against creeps, go into the camp first and take the hits, so our Grunts and heroes lose less health.
""",
    # Raider
    "orai": """\
Raid the enemy back line: go around the fight to enemy siege units (Demolishers, Catapults, Mortar Teams, Meat Wagons, Glaive Throwers) first, then enemy casters and caster heroes; you are offered them wherever they stand.
One or two raiders per target are enough: when its option shows 2 or more of ours already attacking it, pick another back-line target or fight where you are.
Their siege attack wrecks siege units and buildings.
Cast Ensnare on enemy air units to pull them down and on enemy heroes to stop them from escaping.
""",
    # Ghoul
    "ugho": """\
Fight at the front, surround enemy heroes and ranged units, and use Cannibalize on corpses to heal between fights.
""",
    # Abomination
    "uabo": """\
Fight at the front and let Disease Cloud damage nearby enemies.
Attack enemy casters, caster heroes and siege whenever they are in your attack options, before enemy tanks.
""",
    # Mountain Giant
    "emtg": """\
Fight at the front. Before Taunt, walk into the enemy units so they are around the Mountain Giant, and cast it only when its option shows 3 or more enemies within its area; they then attack the Mountain Giant instead of our weaker units.
Use War Club on a tree to deal more damage to buildings.
""",
    # Footman
    "hfoo": """\
Fight at the front and hold the line in front of your casters and ranged units.
Turn Defend on against enemy ranged piercing attacks, and turn it off to move faster when chasing.
""",
    # Water Elemental
    "hwat": """\
A summon that tanks: stand in front of our ranged units and let enemies hit it instead of them.
Shoot enemy casters, ranged units and siege in your attack options before enemy melee units.
""",
    # Spirit Walker
    "ospw": """\
Stay behind your army.
Cast Spirit Link on our frontline units (Tauren, Grunts, melee heroes) so hits on one are shared; recast it when their buffs no longer show Spirit Link.
Disenchant dispels everything in an area: aim it where several enemy summons stand, which it destroys, or on enemies carrying Bloodlust, Inner Fire or Anti-magic Shell. Avoid spots where our own bloodlusted units stand, since it strips their buffs too.
Switch to Ethereal form when enemy melee units attack the Spirit Walker, since physical attacks cannot hurt it then, and switch back to Corporeal form to fight.
Use Ancestral Spirit to bring back a Tauren that died nearby.
""",
}
