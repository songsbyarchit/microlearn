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
    ("physics", "thermodynamics", 4, "Laws governing heat, energy transfer, and entropy"),
    ("physics", "quantum mechanics", 2, "Wave-particle duality and probabilistic nature of matter"),
    ("physics", "newton's laws", 5, "Classical mechanics: inertia, force, and action-reaction"),
    ("physics", "wave theory", 3, "Propagation of mechanical and electromagnetic waves"),
    ("cooking", "maillard reaction", 6, "Chemical browning of proteins and sugars under heat"),
    ("cooking", "emulsification", 4, "Suspending oil and water using emulsifiers like lecithin"),
    ("cooking", "fermentation", 3, "Microbial transformation of sugars to acids and alcohol"),
    ("economics", "game theory", 4, "Strategic decision-making in competitive situations"),
    ("economics", "supply and demand", 5, "Market equilibrium through price and quantity signals"),
    ("economics", "monetary policy", 3, "Central bank control of money supply and interest rates"),
    ("economics", "inflation", 4, "General rise in prices and fall in purchasing power"),
    ("mathematics", "calculus", 3, "Derivatives and integrals: change and accumulation"),
    ("mathematics", "probability", 4, "Quantifying uncertainty and likelihood of outcomes"),
    ("philosophy", "epistemology", 3, "The study of knowledge, belief, and justification"),
    ("philosophy", "ethics", 4, "Moral principles guiding human behaviour"),
    ("music theory", "harmony", 5, "Simultaneous notes and chord progressions"),
    ("music theory", "counterpoint", 3, "Independent melodic lines moving in relation to each other"),
    ("history", "industrial revolution", 5, "Transition from agrarian to industrial economies 1760-1840"),
    ("biology", "natural selection", 6, "Survival advantage of traits suited to environment"),
    ("biology", "cell biology", 4, "Structure and function of the basic unit of life"),
]

# ---------------------------------------------------------------------------
# Transcript pool  (content, is_voice_note)
# ---------------------------------------------------------------------------

TRANSCRIPTS = [
    # Short text questions
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

    # Medium explanations — user explaining a concept back
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

    # Longer voice notes — richer rambling
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
    # Spread nodes across last 30 days
    for i, (domain, topic, bloom, summary) in enumerate(NODES):
        days_ago = random.randint(1, 28)
        upsert_node(domain, topic, bloom, summary, days_ago)

    print("\nSeeding transcripts (30 days, 3-5/day)...")
    transcript_pool = list(TRANSCRIPTS)
    random.shuffle(transcript_pool)

    t_index = 0
    now = datetime.now(tz=timezone.utc)

    for day_offset in range(30, 0, -1):
        day_dt = now - timedelta(days=day_offset)
        count = random.randint(3, 5)
        for _ in range(count):
            if t_index >= len(transcript_pool):
                # Cycle through pool again
                random.shuffle(transcript_pool)
                t_index = 0
            content, is_voice = transcript_pool[t_index]
            t_index += 1

            # Random hour between 08:00 and 22:00
            hour = random.randint(8, 21)
            minute = random.randint(0, 59)
            created_at = day_dt.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()
            insert_transcript(content, is_voice, created_at)

    print("\nDone.")


if __name__ == "__main__":
    seed()
