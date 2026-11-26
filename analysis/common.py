"""评分通用工具：分词、相似度、子项结构。"""
import re

WORD_RE = re.compile(r"[a-z0-9]+(?:['\-][a-z0-9]+)*")

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "with", "in", "on", "to", "from",
    "by", "at", "as", "is", "are", "be", "it", "its", "this", "that", "your",
    "you", "our", "we", "all", "any", "can", "will", "not", "no", "so", "up",
    "out", "into", "than", "then", "more", "most", "other", "also", "just",
    "made", "make", "use", "using", "used", "when", "while", "which", "who",
}


def tokenize(text):
    """英文分词并去停用词，用于重复度与相似度计算。"""
    return [w for w in WORD_RE.findall((text or "").lower())
            if len(w) > 1 and w not in STOPWORDS]


def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def band(value, bands, default=0):
    """bands 为 [(上限, 分值), ...]，返回第一个 value <= 上限 的分值。"""
    for limit, score in bands:
        if value <= limit:
            return score
    return default


def clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, value))


def sub(sid, name, max_score, score, evidence, reason):
    return {
        "id": sid,
        "name": name,
        "max": max_score,
        "score": round(clamp(score, 0, max_score), 1),
        "evidence": evidence,
        "reason": reason,
    }


def total_subs(subs):
    return round(sum(s["score"] for s in subs), 1)


def issue(severity, finding, fix, dimension):
    return {"dimension": dimension, "severity": severity, "finding": finding, "fix": fix}
