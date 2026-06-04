"""
seed_data.py — Populate Supabase with 30 days of realistic sample data.
Run once: python seed_data.py
"""
import json
import os
import random
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

from supabase_client import sb_headers, sb_url

random.seed(42)

# ---------------------------------------------------------------------------
# Knowledge nodes
# ---------------------------------------------------------------------------

NODES = [
    # Physics
    ("physics", "thermodynamics", 4, "Laws governing heat, energy transfer, and entropy"),
    ("physics", "quantum mechanics", 2, "Wave-particle duality and probabilistic nature of matter"),
    ("physics", "newton's laws", 5, "Classical mechanics: inertia, force, and action-reaction"),
    ("physics", "wave theory", 3, "Propagation of mechanical and electromagnetic waves"),
    # Cooking
    ("cooking", "maillard reaction", 6, "Chemical browning of proteins and sugars under heat"),
    ("cooking", "emulsification", 4, "Suspending oil and water using emulsifiers like lecithin"),
    ("cooking", "fermentation", 3, "Microbial transformation of sugars to acids and alcohol"),
    # Economics
    ("economics", "game theory", 4, "Strategic decision-making in competitive situations"),
    ("economics", "supply and demand", 5, "Market equilibrium through price and quantity signals"),
    ("economics", "monetary policy", 3, "Central bank control of money supply and interest rates"),
    ("economics", "inflation", 4, "General rise in prices and fall in purchasing power"),
    # Mathematics
    ("mathematics", "calculus", 3, "Derivatives and integrals: change and accumulation"),
    ("mathematics", "probability", 4, "Quantifying uncertainty and likelihood of outcomes"),
    # Philosophy
    ("philosophy", "epistemology", 3, "The study of knowledge, belief, and justification"),
    ("philosophy", "ethics", 4, "Moral principles guiding human behaviour"),
    # Music theory
    ("music theory", "harmony", 5, "Simultaneous notes and chord progressions"),
    ("music theory", "counterpoint", 3, "Independent melodic lines moving in relation to each other"),
    # History
    ("history", "industrial revolution", 5, "Transition from agrarian to industrial economies 1760-1840"),
    # Biology
    ("biology", "natural selection", 6, "Survival advantage of traits suited to environment"),
    ("biology", "cell biology", 4, "Structure and function of the basic unit of life"),
    # Neuroscience (new)
    ("neuroscience", "neuroplasticity", 4, "The brain's ability to reorganise and form new connections"),
    ("neuroscience", "dopamine system", 5, "Reward prediction, motivation, and reinforcement learning"),
    ("neuroscience", "memory consolidation", 3, "Transfer of information from short-term to long-term memory"),
    # Linguistics (new)
    ("linguistics", "syntax", 3, "Rules governing sentence structure across languages"),
    ("linguistics", "phonology", 4, "Sound systems and patterns within languages"),
    ("linguistics", "language acquisition", 5, "How children learn language naturally and rapidly"),
    # Architecture (new)
    ("architecture", "load-bearing structures", 4, "How forces travel through arches, beams, and columns"),
    ("architecture", "vernacular architecture", 3, "Buildings shaped by local climate, materials, and culture"),
    # Investing (new)
    ("investing", "compound interest", 6, "Exponential growth of returns reinvested over time"),
    ("investing", "portfolio diversification", 4, "Reducing risk by spreading investments across asset classes"),
    ("investing", "index funds", 5, "Passive market-tracking funds with low fees and broad exposure"),
]

# ---------------------------------------------------------------------------
# Transcript pool  (content, is_voice_note)
# ---------------------------------------------------------------------------

TRANSCRIPTS = [
    # ── Short text questions ─────────────────────────────────────────────────
    ("what actually is entropy? like in simple terms", False),
    ("why does ice float on water? it's a solid so shouldn't it sink?", False),
    ("can you explain the maillard reaction? i keep hearing about it in cooking videos", False),
    ("what is game theory? is it actually about games?", False),
    ("what's the difference between monetary and fiscal policy?", False),
    ("how does natural selection actually work? like the mechanism of it", False),
    ("what does quantum actually mean? i feel like the word gets thrown around everywhere", False),
    ("explain harmony in music, why do certain notes sound good together?", False),
    ("what made the industrial revolution happen when it did?", False),
    ("what is emulsification and why does it matter for cooking?", False),
    ("is probability objective or subjective? i've been arguing about this", False),
    ("what's epistemology in simple terms?", False),
    ("what is neuroplasticity and does it actually mean you can change your brain?", False),
    ("why does dopamine get called the pleasure chemical if that's not quite right?", False),
    ("how do index funds work and why does everyone say they beat active managers?", False),
    ("what is compound interest and why does starting early matter so much?", False),
    ("how do arches work structurally? why don't they just collapse?", False),
    ("what's the critical period for language acquisition and why does it close?", False),

    # ── Medium explanations — user explaining back ────────────────────────────
    (
        "ok so i think i understand entropy now. basically it's like the universe tends toward "
        "disorder right? so if you spill milk it doesn't just go back into the glass on its own. "
        "the number of ways the milk can be spread around the floor is way higher than the number "
        "of ways it can be in the glass. so entropy is sort of like a measure of how many possible "
        "arrangements there are? is that roughly right",
        True,
    ),
    (
        "so the maillard reaction is basically when proteins and sugars react at high temperatures "
        "and create those brown flavours, like when you sear a steak or toast bread. the key thing "
        "is it needs heat above about 140 celsius and it's different from caramelisation which is "
        "just about sugar. did i get that right?",
        True,
    ),
    (
        "right so game theory is about making decisions when other people's choices affect your outcome. "
        "the classic one is the prisoner's dilemma where both players would be better off cooperating "
        "but individually they're each tempted to defect. and nash equilibrium is when neither player "
        "can improve by changing strategy alone, sort of a stable state. does that sound right?",
        False,
    ),
    (
        "ok natural selection -- organisms with traits better suited to their environment are more "
        "likely to survive and reproduce. over many generations those traits become more common in the "
        "population. it's not that individuals evolve, populations evolve over time through differential "
        "reproduction rates. selection pressure is just whatever makes some traits more or less useful",
        False,
    ),
    (
        "so harmony is basically about which notes sound good simultaneously, and it's related to "
        "frequency ratios? like an octave is 2:1 and a perfect fifth is 3:2 and those simple ratios "
        "feel consonant because the waveforms align regularly. and chord progressions create tension "
        "and resolution by moving between stable and unstable harmonies",
        True,
    ),
    (
        "fermentation is basically microbes consuming sugars and producing other things as byproducts, "
        "like yeast producing alcohol and CO2, or bacteria producing lactic acid in yoghurt. the key "
        "insight is you're harnessing microbial metabolism to transform food. and you can control "
        "what you get by controlling which microbes are present and the conditions they're in",
        True,
    ),

    # ── Longer voice notes — original ────────────────────────────────────────
    (
        "so i've been thinking about what you said about thermodynamics and i think i've been confusing "
        "entropy with disorder for a while. like i always thought entropy just meant things getting messier "
        "but that's not quite right is it? because you can have a highly ordered system that also has high "
        "entropy in the thermodynamic sense. i think the key insight is that entropy is really about the "
        "number of microstates available to the system. so a gas has high entropy not because it's disordered "
        "in some aesthetic sense but because there are astronomically more ways for gas molecules to be spread "
        "around a room than for them all to be in one corner. does that mean entropy is fundamentally a "
        "statement about probability rather than disorder? because that reframes it quite a lot for me. "
        "it's not that the universe is getting messier, it's that the universe is exploring probability space",
        True,
    ),
    (
        "you know i was trying to explain game theory to a friend and i realised i don't fully understand "
        "when it applies to real life. like in experiments people cooperate way more than pure game theory "
        "would predict. is that because the model assumes one-shot games but real life is repeated? or is it "
        "because humans have genuinely other-regarding preferences not captured in simple utility functions? "
        "i feel like the answer is probably both but i'm not sure how to think about that. also i've been "
        "reading about how game theory applies to evolutionary biology through things like evolutionarily "
        "stable strategies, and that's kind of blowing my mind a bit because it means you don't even need "
        "conscious decision making for game-theoretic dynamics to emerge",
        True,
    ),
    (
        "so i was thinking about inflation and basically i think it's sort of like a hidden tax right? "
        "because if the money supply grows faster than the actual goods and services in the economy then "
        "each unit of currency buys less. but what i don't understand is why central banks target like "
        "2 percent inflation rather than zero. surely zero would be better? i've heard something about "
        "deflation being worse but i don't really get why. if prices are falling shouldn't that be good "
        "for consumers? i guess there's something about debt dynamics and incentives to delay purchases "
        "but i'd love to understand it more clearly. also is the 2 percent target kind of arbitrary or "
        "is there actual economic reasoning behind that specific number",
        True,
    ),
    (
        "i was trying to understand counterpoint and why the rules exist. like the prohibition on parallel "
        "fifths seems almost arbitrary at first but i think it's because parallel fifths cause the two voices "
        "to merge perceptually and you lose the sense of independence between the lines. the whole point of "
        "counterpoint is that you have genuinely separate melodic lines that are also harmonically coherent "
        "together. and the rules about resolving dissonances make sense because tension needs release. "
        "what i'm less clear on is whether these rules are universal or culturally specific. like did bach "
        "follow them because they're somehow mathematically fundamental or because that was the aesthetic of "
        "his time and place? i suspect it's more the latter but the mathematical arguments about frequency "
        "ratios make me unsure",
        True,
    ),
    (
        "so i've been thinking about probability and whether it's objective or subjective. the frequentist "
        "view says probability is a property of the world, it's the long-run frequency of an event. but the "
        "bayesian view says probability is a degree of belief, it's in the mind of the observer. and these "
        "aren't just philosophical positions they actually lead to different statistical methods. i think "
        "the bayesian view is more coherent for one-off events like what's the probability it rains tomorrow "
        "because there's no long run to appeal to. but i can see why frequentists are suspicious of "
        "subjective priors. anyway i think what i'm confused about is what it would even mean for probability "
        "to be objective in some deep sense",
        True,
    ),

    # ── NEW: 20 longer voice notes with fillers ───────────────────────────────
    (
        "so i've been reading about neuroplasticity and you know it's sort of one of those concepts that "
        "sounds almost too good to be true? like the idea that the brain can basically rewire itself in "
        "response to experience. i understand at the cellular level it's about synapse strengthening -- "
        "neurons that fire together wire together and all that. but what i'm struggling with is the limits. "
        "like obviously you can't regrow a severed spinal cord just by thinking hard. so there must be "
        "different types of plasticity and some are more constrained than others? i think there's a "
        "distinction between experience-dependent plasticity which happens throughout life and the sort of "
        "critical period stuff that closes off in development. you know what i'd really like to understand "
        "is whether meditation or deliberate practice literally changes the physical structure of the brain "
        "or whether that's sort of overstating what the neuroscience actually shows",
        True,
    ),
    (
        "ok so dopamine is literally not just about pleasure right, you said it's more about anticipation "
        "and prediction error? so basically the dopamine spike happens when you get something unexpected "
        "and good, and if something you expected to be rewarding doesn't happen you get a sort of dip. "
        "that's kind of wild because it means the system is basically computing the difference between "
        "what you predicted and what actually happened. and that's you know essentially what machine "
        "learning systems do with reward signals. so in a way evolution sort of discovered reinforcement "
        "learning before we did? i'm also wondering whether this explains why variable reward schedules "
        "are so addictive -- slot machines, social media notifications -- because you never quite know "
        "when the reward is coming so the anticipation never fully habituates",
        True,
    ),
    (
        "i've been thinking about memory consolidation and basically what i understand is that sleep is "
        "sort of crucial for it? like during the day you're basically taking notes and during sleep your "
        "brain is filing them properly. the hippocampus holds memories temporarily and then during sleep "
        "they get transferred to the cortex for long-term storage. what i don't fully get is why this "
        "needs to happen offline -- like why can't consolidation happen while you're awake? is it because "
        "the brain needs to be quiet to do that kind of maintenance work? you know i also wonder about "
        "the emotional tagging that happens during consolidation. i've read that the amygdala is involved "
        "and that emotional memories are consolidated differently. which sort of explains why traumatic "
        "memories can be so vivid and intrusive -- they're basically flagged as high priority",
        True,
    ),
    (
        "so language acquisition is basically one of those things that seems miraculous when you actually "
        "think about it. like children go from babbling to basically fluent speakers in a few years and "
        "they do it without anyone explicitly teaching them grammar rules. chomsky's argument was that "
        "this is only possible if there's some kind of innate language faculty -- a sort of universal "
        "grammar that constrains what languages can look like. but i find that sort of unsatisfying "
        "because it kind of just pushes the question back -- where does the innate structure come from? "
        "the statistical learning view is more appealing to me -- that children are basically just "
        "incredibly good at finding patterns in the input they receive. you know what's interesting "
        "is that deaf children who aren't exposed to any language will literally invent their own "
        "sign language with consistent grammatical structure. that's kind of hard to explain without "
        "some innate push toward language",
        True,
    ),
    (
        "right so i've been trying to understand syntax and basically what strikes me is how much "
        "structure is hidden in what seems like a simple sentence. like you know there's this famous "
        "ambiguous sentence -- time flies like an arrow, fruit flies like a banana. and the reason "
        "it's funny is that the same words can be parsed in completely different ways. so syntax is "
        "basically the rules that tell you which parsing is the right one in context. what i don't "
        "understand is whether these rules are universal -- like does every language have essentially "
        "the same deep structure even if the surface looks different? japanese puts verbs at the end "
        "and english puts them in the middle but maybe there's sort of a common underlying logic. "
        "or is that just wishful thinking and languages are actually fundamentally different in their "
        "structure in ways that affect how speakers think",
        True,
    ),
    (
        "so load-bearing structures in architecture -- basically i think the key insight is that "
        "arches work because compression is transferred laterally. like a stone arch doesn't need "
        "any mortar in tension because the weight of the stones above literally pushes each stone "
        "into its neighbours and the whole thing stays up. that's why you need the buttresses on "
        "gothic cathedrals -- they're basically absorbing the outward thrust that the arches are "
        "transferring. you know what i find genuinely beautiful about this is that it's sort of "
        "pure material logic. stone is strong in compression and weak in tension, so you design "
        "structures that keep everything in compression. concrete is the opposite story -- it's "
        "strong in compression but steel reinforcement deals with the tension. i wonder if the "
        "kind of intuitive structural understanding that master builders had before engineering "
        "as a discipline existed was literally passed down through apprenticeships for centuries",
        True,
    ),
    (
        "i've been thinking about vernacular architecture and you know it's basically this idea that "
        "buildings designed without architects often end up being perfectly adapted to their climate "
        "and culture. like the thick adobe walls in the american southwest that keep the interior "
        "cool during the day because they have high thermal mass. or the raised houses in flood-prone "
        "areas of south-east asia. or the way traditional japanese buildings use sliding screens "
        "instead of solid walls to manage ventilation. basically the argument is that local building "
        "traditions encode centuries of practical knowledge about what works in a particular environment. "
        "and sort of the critique of modernist architecture is that it ignored all of this in favour "
        "of a universal aesthetic that works poorly in many climates. glass towers in hot sunny cities "
        "are literally the opposite of what makes sense thermally. i wonder how much of this wisdom "
        "is being recovered in sustainable architecture now",
        True,
    ),
    (
        "compound interest is sort of the thing that sounds simple but when you actually run the "
        "numbers it's literally shocking. like if you invest a thousand pounds at 7 percent annual "
        "return and just leave it, in 40 years it becomes like fifteen thousand pounds. which means "
        "you know money basically doubles roughly every ten years at that rate -- that's the rule of "
        "72 right, you divide 72 by the interest rate to get the doubling time. what i find interesting "
        "is the flip side with debt -- compound interest works exactly the same way against you if "
        "you're carrying high-interest debt. a credit card at 20 percent is basically doubling your "
        "debt every three and a half years if you're not paying it down. so i think the core insight "
        "is that time is the most important variable, not the rate, not the initial amount. starting "
        "ten years earlier basically doubles your outcome even if you contribute the same amount",
        True,
    ),
    (
        "so index funds -- the basic argument is sort of embarrassingly simple once you hear it. "
        "most professional fund managers don't consistently beat the market after fees. so rather "
        "than paying someone to try and pick winners you just buy the whole market and by definition "
        "get the market return. what i find interesting is the sort of paradox here -- if everyone "
        "indexed, prices would never reflect new information because no one would be doing the work "
        "of price discovery. so active managers are basically a necessary evil that makes the market "
        "efficient enough for passive investing to work. i also don't fully understand whether this "
        "logic holds in less efficient markets like small-cap stocks or emerging markets where there's "
        "maybe more opportunity for active managers to find genuinely mispriced assets. you know the "
        "evidence seems pretty overwhelming in developed large-cap markets though",
        True,
    ),
    (
        "portfolio diversification is basically the only free lunch in investing apparently -- "
        "you can reduce risk without necessarily reducing expected return by combining assets that "
        "don't move in lockstep. the classic example is mixing stocks and bonds because they tend "
        "to be negatively correlated. but you know what i'm not sure about is how well this holds "
        "in a real crisis. like in 2008 correlations between asset classes kind of spiked because "
        "everyone was selling everything at once. so the diversification benefit sort of disappears "
        "exactly when you need it most. i think the sophisticated version of this is to think about "
        "diversifying across risk factors rather than just asset classes -- like value, momentum, "
        "quality -- because those have different underlying economic drivers and might stay more "
        "genuinely uncorrelated even in stress scenarios",
        True,
    ),
    (
        "i've been trying to get my head around quantum mechanics and basically what confuses me "
        "is the measurement problem. like the wave function describes a superposition of states "
        "but when you measure it you get a definite result. where does the superposition go? "
        "the copenhagen interpretation sort of just says the wave function collapses and doesn't "
        "ask why. the many-worlds interpretation says all outcomes actually happen in branching "
        "universes which is you know sort of ontologically extravagant but mathematically clean. "
        "what i find genuinely hard to wrap my head around is that quantum effects are literally "
        "real at the level of chemistry and biology -- photosynthesis apparently uses quantum "
        "coherence to transfer energy efficiently. so this isn't just abstract weirdness, it's "
        "woven into how life works at the molecular level",
        True,
    ),
    (
        "ok so wave theory -- i think what i'm starting to understand is that waves are basically "
        "just a pattern of energy propagating through a medium rather than matter itself moving. "
        "like in a water wave the water molecules are sort of going up and down but not actually "
        "travelling horizontally. the energy travels horizontally. and for sound waves the air "
        "molecules are compressing and expanding but again not really going anywhere. so what's "
        "interesting is what happens when there's no medium -- electromagnetic waves. you know "
        "they literally don't need anything to travel through which is why light can cross empty "
        "space. and the wave-particle duality of light means it behaves like a wave in some "
        "experiments and like a particle in others. basically the question of what light really "
        "is might not have a satisfying answer in ordinary intuitive terms",
        True,
    ),
    (
        "newton's third law is one of those things that sounds obvious but is sort of counterintuitive "
        "when you apply it. like if i push on a wall the wall pushes back on me with equal and opposite "
        "force. but then why does the wall not move? it's because the forces are on different objects -- "
        "the force on the wall from me and the force on me from the wall are equal and opposite but "
        "they don't cancel because they're not on the same object. you know what trips people up is "
        "thinking about a horse pulling a cart. if the cart pulls the horse backward with equal force "
        "how does anything move? and the answer is you have to look at all the forces on each object "
        "separately -- the horse's feet push against the ground and the ground reaction is what "
        "actually accelerates the horse-cart system forward. basically you can't understand mechanics "
        "without being really careful about what's the system you're analysing",
        True,
    ),
    (
        "i've been thinking about ethics and basically what strikes me is that all the major ethical "
        "theories seem to capture something real but none of them works as a complete system. "
        "consequentialism says what matters is outcomes -- maximise wellbeing. but that seems to "
        "justify terrible things if the consequences are good enough. deontology says some things "
        "are just wrong regardless of consequences -- you know, lying, using people as mere means. "
        "but rigid deontology seems sort of cruel when following the rule causes obvious preventable "
        "harm. virtue ethics says focus on character rather than rules or consequences -- what would "
        "a good person do? which is more flexible but can seem circular. i wonder if the reason moral "
        "philosophy hasn't converged on an answer after thousands of years is because ethics isn't "
        "really a system to be discovered but more like a practice of navigating genuine tension "
        "between things that all matter",
        True,
    ),
    (
        "epistemology is basically asking how do we know what we know and i think what's interesting "
        "is that the question applies to itself. like how do we know that our methods for acquiring "
        "knowledge are reliable? you can't use induction to justify induction without circularity. "
        "hume basically showed that all our beliefs about cause and effect and the future resembling "
        "the past are sort of unjustifiable by pure reason. we just sort of trust them because they've "
        "worked so far. what i find fascinating is the practical implications. science is basically "
        "the most successful knowledge-generating enterprise humans have built but it rests on "
        "inductive inference which hume showed can't be fully justified. does that mean scientific "
        "knowledge is fundamentally uncertain? i think the honest answer is yes but that's not a "
        "reason for despair -- it just means you hold beliefs with appropriate degrees of confidence "
        "rather than treating anything as absolutely certain",
        True,
    ),
    (
        "so calculus -- i think the derivative is actually more intuitive than it seemed at first. "
        "basically it's just asking how fast is this function changing at this exact point. and the "
        "way you get there is by looking at the average rate of change over smaller and smaller "
        "intervals and seeing what it converges to. what i find genuinely beautiful is that the "
        "derivative and the integral are sort of inverse operations -- like differentiation undoes "
        "integration and vice versa. and you know the fundamental theorem of calculus which connects "
        "them is apparently one of the most important results in mathematics but the statement itself "
        "is not that hard to understand intuitively. the area under a curve is basically accumulation "
        "and the derivative is basically the rate of change and they're literally mirror images of "
        "each other in a deep sense",
        True,
    ),
    (
        "cell biology is sort of mind-blowing when you start to appreciate the scale of what's "
        "happening. like a single cell contains basically all the machinery of life -- it can take "
        "in nutrients, produce energy, replicate its own DNA, respond to signals from outside. "
        "and you know what gets me is the mitochondria story -- they were apparently once separate "
        "bacteria that got engulfed by a larger cell and instead of being digested they formed a "
        "partnership. which is basically the origin of all complex life. the evidence is that "
        "mitochondria still have their own DNA separate from the cell's nucleus. i also find it "
        "hard to wrap my head around how a fertilised egg with one set of DNA can turn into an "
        "organism with hundreds of different cell types all containing the same DNA. how does a "
        "liver cell know to be a liver cell and not a neuron? it's literally all about which genes "
        "are switched on and off in each cell type",
        True,
    ),
    (
        "so supply and demand -- the thing i find sort of non-obvious is how prices actually do "
        "the coordinating work. like you don't need any central authority to decide how much wheat "
        "to produce or where to ship it. if there's a drought in one region the price of wheat goes "
        "up, which signals farmers elsewhere to grow more and consumers to find substitutes. the "
        "information is sort of encoded in the price signal. hayek's point was that this local "
        "distributed information could never be gathered centrally fast enough to plan production "
        "effectively. but you know what i think the model struggles with is situations where prices "
        "don't capture all the costs -- like environmental externalities. if the price of coal "
        "doesn't include the cost of the carbon it emits then the market literally produces the "
        "wrong amount of coal from a social welfare perspective. so the market is a brilliant "
        "mechanism for aggregating information but it needs the right prices to work properly",
        True,
    ),
    (
        "the industrial revolution is one of those things where you know it's tempting to think "
        "it was basically inevitable once you had the steam engine. but i'm not sure that's right. "
        "like why did it happen in britain first and not china which was arguably more advanced "
        "technologically for centuries? i think the answer is something to do with the specific "
        "combination of factors -- coal deposits near population centres, a legal framework that "
        "protected property rights and enabled capital accumulation, a culture of practical "
        "experimentation, and sort of the particular labour costs that made it economically "
        "rational to invest in labour-saving machinery. basically the steam engine didn't cause "
        "the industrial revolution on its own -- it was more like the conditions were right and "
        "the steam engine was the catalyst. i wonder how much of historical causation is like that -- "
        "we identify the trigger event but the underlying pressures were what actually mattered",
        True,
    ),
    (
        "emulsification is literally one of those techniques that once you understand the chemistry "
        "you realise it explains so much about cooking. basically you need a molecule that has one "
        "end that's attracted to water and one end attracted to fat -- that's an emulsifier like "
        "lecithin in egg yolk. and the emulsifier sort of positions itself at the interface between "
        "the oil and water droplets and prevents them from coalescing. which is why mayonnaise "
        "doesn't separate -- the egg yolk lecithin is holding the oil droplets suspended in the "
        "water. what i find interesting is the stability question. like a hollandaise sauce can "
        "break if it gets too hot because the proteins that are also helping stabilise it "
        "coagulate and lose their emulsifying function. so you know there are temperature limits "
        "to emulsification that are sort of built into the physics of the proteins involved. "
        "and the ratio of oil to emulsifier matters -- you can only stabilise so many oil droplets "
        "with a given amount of emulsifier before the sauce breaks",
        True,
    ),
]


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------

def upsert_node(domain: str, topic: str, bloom: int, summary: str, days_ago: int) -> None:
    updated = (datetime.now(tz=timezone.utc) - timedelta(days=days_ago)).isoformat()
    content = f"""# {topic.title()}

domain: {domain}
bloom_level: {bloom}
last_updated: {updated}

## Summary
{summary}

## Edges
(none)

## Vocabulary
(none)

## History
- {updated}: bloom_score updated to {bloom}
"""
    row = {
        "domain": domain,
        "topic": topic,
        "content": content,
        "bloom_score": bloom,
        "edges": [],
        "updated_at": updated,
    }
    resp = httpx.post(
        sb_url("/rest/v1/knowledge_nodes"),
        headers=sb_headers(prefer="resolution=merge-duplicates"),
        params={"on_conflict": "domain,topic"},
        content=json.dumps(row),
        timeout=10,
    )
    resp.raise_for_status()
    print(f"  node: {domain}/{topic} (bloom {bloom})")


def insert_transcript(content: str, is_voice_note: bool, created_at: str) -> None:
    word_count = len(content.split())
    row = {
        "content": content,
        "word_count": word_count,
        "is_voice_note": is_voice_note,
        "created_at": created_at,
    }
    resp = httpx.post(
        sb_url("/rest/v1/transcripts"),
        headers=sb_headers(),
        content=json.dumps(row),
        timeout=10,
    )
    resp.raise_for_status()
    print(f"  transcript: {word_count}w | voice={is_voice_note} | {created_at[:10]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def seed() -> None:
    print("Seeding knowledge nodes...")
    for domain, topic, bloom, summary in NODES:
        days_ago = random.randint(1, 28)
        upsert_node(domain, topic, bloom, summary, days_ago)

    print(f"\nSeeding transcripts (30 days, 5-8/day)...")
    transcript_pool = list(TRANSCRIPTS)
    random.shuffle(transcript_pool)

    t_index = 0
    now = datetime.now(tz=timezone.utc)

    for day_offset in range(30, 0, -1):
        day_dt = now - timedelta(days=day_offset)
        count = random.randint(5, 8)
        for _ in range(count):
            if t_index >= len(transcript_pool):
                random.shuffle(transcript_pool)
                t_index = 0
            content, is_voice = transcript_pool[t_index]
            t_index += 1

            hour = random.randint(8, 21)
            minute = random.randint(0, 59)
            created_at = day_dt.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()
            insert_transcript(content, is_voice, created_at)

    print("\nDone.")


if __name__ == "__main__":
    seed()
