#!/usr/bin/env python3
"""enrich.py — 路径 A 漏斗第①级：确定性预排序 + 摘要补齐 + 尾部抽样。

评分：LLM 语境词 × 效率主题词（可调）；top-K 走 OpenAlex 标题检索补摘要
（匹配校验：词元重合 ≥0.7），失败回退 arXiv。尾部按固定种子随机抽样 N 条
（供漏检率上界，判断由 agent 按 SKILL.md 第 4 节执行）。
"""
import argparse, json, os, random, re, sys, time, hashlib, urllib.request, urllib.parse

LLM_WORDS = ["llm", "language model", "transformer", "gpt", "llama", "foundation model",
             "text-to-text", "autoregressive", "chat", "instruction-tun"]
EFF_STRONG = ["quantiz", "low-bit", "1-bit", "w4a4", "awq", "gptq", "prun", "spars",
              "compress", "kv cache", "kv-cache", "key-value cache", "accelerat",
              "speculative decoding", "distillation", "throughput", "latency",
              "memory footprint", "on-device", "edge", "efficient", "fast ", "speedup",
              "mixture-of-experts", "moe ", "decoding"]
EFF_WEAK = ["inference", "deploy", "serving", "scaling", "optimiz"]

def score(title):
    t = " " + title.lower() + " "
    llm = any(w in t for w in LLM_WORDS)
    s = 2 * int(llm)
    if any(w in t for w in EFF_STRONG): s += 2
    elif any(w in t for w in EFF_WEAK): s += 1
    return s

def crossenc_scores(titles, topic_desc, cache_dir, model_name="cross-encoder/ms-marco-MiniLM-L-6-v2"):
    """交叉编码器重排（①级同分排序器，2026-10-26 回归定稿：四项指标全胜 bge 同分）。
    query=主题描述，doc=标题；MPS/CPU 约 1ms/条。按 (模型, 文本哈希) 缓存。"""
    import hashlib as _h
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        return None, "sentence-transformers 未安装"
    import numpy as np
    cdir = os.path.join(cache_dir, "embed", "ce_" + model_name.replace("/", "_"))
    os.makedirs(cdir, exist_ok=True)
    tag = _h.md5((topic_desc + "|" + "\n".join(titles)).encode()).hexdigest()[:12]
    cf = os.path.join(cdir, tag + ".npy")
    if os.path.exists(cf):
        return np.load(cf), "cached"
    import torch
    ce = CrossEncoder(model_name, max_length=256, device="mps" if torch.backends.mps.is_available() else "cpu")
    scores = np.array(ce.predict([(topic_desc, t) for t in titles], batch_size=64, show_progress_bar=False))
    np.save(cf, scores)
    return scores, None

def vector_scores(titles, topic_desc, cache_dir, model_name="BAAI/bge-small-en-v1.5", centroid_train=None):
    """本地嵌入排序（①级主力）。两种模式：
    - centroid_train=[(text,label),...] 给出历史判断/种子 → 各类中心，score=到 core 中心相似度（回归验证：4 漏检全部进前 16）
    - 缺省 → 主题余弦（无标签时兜底）
    嵌入按 (模型, 文本哈希) 缓存。"""
    import hashlib as _h
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None, "sentence-transformers 未安装（uv venv --python 3.12 + uv pip install sentence-transformers）"
    import numpy as np
    cdir = os.path.join(cache_dir, "embed", model_name.replace("/", "_"))
    os.makedirs(cdir, exist_ok=True)
    model = SentenceTransformer(model_name)
    def embed(texts, tag):
        cf = os.path.join(cdir, tag + ".npy")
        if os.path.exists(cf):
            return np.load(cf)
        v = model.encode(texts, normalize_embeddings=True, show_progress_bar=False, batch_size=128)
        np.save(cf, v)
        return v
    key = _h.md5(("\n".join(titles)).encode()).hexdigest()[:10]
    docs = embed(titles, "docs_" + key)
    if centroid_train:
        tr_texts, tr_labels = zip(*centroid_train)
        trk = _h.md5(("\n".join(tr_texts) + "|" + ",".join(tr_labels)).encode()).hexdigest()[:10]
        tr = embed(list(tr_texts), "train_" + trk)
        core_mask = np.array([l == "core" for l in tr_labels])
        cent = tr[core_mask].mean(0); cent = cent / np.linalg.norm(cent)
        return docs @ cent, "centroid(core)"
    qkey = _h.md5(topic_desc.encode()).hexdigest()[:10]
    tv = embed([topic_desc], "topic_" + qkey)[0]
    return docs @ tv, "topic-cosine"

def toks(s):
    return set(re.findall(r"[a-z0-9]+", s.lower()))

def openalex_title(title, cache_dir):
    url = ("https://api.openalex.org/works?filter=title.search:" + urllib.parse.quote(f'"{title}"') +
           "&per-page=1&select=id,doi,title,authorships,publication_year,primary_location,abstract_inverted_index,cited_by_count,best_oa_location")
    cf = os.path.join(cache_dir, "api", "oa_title", hashlib.md5(url.encode()).hexdigest() + ".json")
    os.makedirs(os.path.dirname(cf), exist_ok=True)
    if os.path.exists(cf): data = json.load(open(cf, encoding="utf-8"))
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-cat/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r: data = json.load(r)
        except Exception as e:
            return None, f"openalex {type(e).__name__}"
        json.dump(data, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
        time.sleep(0.4)
    res = (data.get("results") or [None])[0]
    if not res: return None, "no_result"
    tt = toks(title)
    if not tt or len(toks(res.get("title", "")) & tt) / len(tt) < 0.7:
        return None, "title_mismatch"
    return res, "ok"

def abstract_from_inv(inv):
    if not inv: return ""
    pos = {i: w for w, idxs in inv.items() for i in idxs}
    return " ".join(pos[i] for i in sorted(pos))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--topic-name", default="高效 LLM 推理")
    ap.add_argument("--top", type=int, default=300)
    ap.add_argument("--tail-sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-score", type=int, default=2, help="进入富集池的最低分")
    ap.add_argument("--rerank", choices=["auto", "vector", "keyword"], default="auto",
                    help="主排序：vector=本地嵌入余弦（需 venv sentence-transformers），keyword=词元评分")
    ap.add_argument("--topic-desc", default="", help="主题描述（向量排序的查询文本；缺省用 topic-name")
    ap.add_argument("--centroid-from", default="", metavar="TASKDIR",
                    help="任务目录（内含 ranked.jsonl + judgments.jsonl）→ 抽 (标题,判定) 训练 core 类中心（①级推荐模式）")
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/paper-cat"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--out-tail", required=True)
    a = ap.parse_args()
    with open(a.catalog, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f]
    custom_topic = bool(a.topic_desc or a.topic_name != "高效 LLM 推理")
    if custom_topic and a.rerank == "keyword":
        sys.exit("自定义主题需语义排序；内置词元表仅适用于高效 LLM 推理")
    for r in rows: r["_score"] = 0 if custom_topic else score(r["title"])
    topic_desc = a.topic_desc or a.topic_name
    centroid_train = None
    if a.centroid_from:
        rpath = os.path.join(a.centroid_from, "ranked.jsonl")
        jpath = os.path.join(a.centroid_from, "judgments.jsonl")
        rrows = [json.loads(l) for l in open(rpath, encoding="utf-8")]
        jmap = {j["idx"]: j for j in (json.loads(l) for l in open(jpath, encoding="utf-8")) if "idx" in j}
        centroid_train = [(r["title"], jmap[i]["decision"]) for i, r in enumerate(rrows)
                          if jmap.get(i, {}).get("decision") in ("core", "periphery", "exclude")]
        print(f"  类中心训练例 {len(centroid_train)} 条（来自 {a.centroid_from}）")
    ces, ces_mode = None, None
    sims, mode = None, None
    topic_en = a.topic_desc or (a.topic_name if custom_topic else "efficient inference of large language models: quantization, pruning, "
                                "KV cache, acceleration, parallel decoding, MoE compression, "
                                "memory-efficient deployment, model compression")
    if a.rerank in ("auto", "vector"):
        ces, ces_mode = crossenc_scores([r["title"] for r in rows], topic_en, a.cache_dir)
        if ces is None:
            sims, mode = vector_scores([r["title"] for r in rows], topic_desc, a.cache_dir, centroid_train=centroid_train)
        if ces is None and sims is None and (a.rerank == "vector" or custom_topic):
            sys.exit(f"向量/交叉编码器均不可用：{ces_mode} / {mode}")
    if sims is not None or ces is not None:
        # ①级定稿（2026-10-26 ICLR 全目录回归，策略 D 四项指标全胜）：
        # 词元分数主层（召回地板，4 漏检均词元满分）+ 交叉编码器同分排序（替代字母序/bge）。
        # bge 类中心可作第三键。纯 CE/纯向量主排序均会把词元命中的漏检挤到 100-471 名。
        tie = ces if ces is not None else (sims if sims is not None else [0.0] * len(rows))
        for r, s in zip(rows, tie): r["_tie"] = float(s)
        rows.sort(key=lambda r: (-r["_score"], -r["_tie"], r["dblp_key"]))
        ranked = (f"semantic({'CE' if ces is not None else mode})" if custom_topic else
                  f"hybrid(词元主层+{'CE' if ces is not None else mode}同分)")
    else:
        rows.sort(key=lambda r: (-r["_score"], r["dblp_key"]))
        ranked = f"keyword（向量不可用：{mode}）"
    pool = rows if custom_topic else [r for r in rows if r["_score"] >= a.min_score]
    top = pool[:a.top]
    tail_pool = pool[a.top:]
    rng = random.Random(a.seed)
    tail = rng.sample(tail_pool, min(a.tail_sample, len(tail_pool))) if tail_pool else []

    out, missing = [], 0
    for r in top:
        rec = {k: v for k, v in r.items() if not k.startswith("_")}
        rec.update({"prefilter_score": r["_score"], "vector_score": r.get("_vec"),
                    "topic": a.topic_name, "rerank": ranked,
                    "externalIds": {"DBLP": r["dblp_key"]}})
        res, why = openalex_title(r["title"], a.cache_dir)
        if res:
            src = ((res.get("primary_location") or {}).get("source") or {})
            rec.update({"abstract": abstract_from_inv(res.get("abstract_inverted_index")),
                        "doi": (res.get("doi") or "").replace("https://doi.org/", ""),
                        "venue": src.get("display_name") or "",
                        "venue_issn": (src.get("issn") or [None])[0],
                        "citations": res.get("cited_by_count"),
                        "openAccessPdf": ((res.get("best_oa_location") or {}).get("pdf_url")),
                        "enrich": "openalex"})
        else:
            rec.update({"abstract": "", "enrich": f"failed:{why}"})
            missing += 1
        out.append(rec)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(a.out_tail, "w", encoding="utf-8") as f:
        for r in tail: f.write(json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, ensure_ascii=False) + "\n")

    n_below = 0 if custom_topic else sum(1 for r in rows if r["_score"] < a.min_score)
    print(f"排序方式：{ranked}")
    print(f"目录 {len(rows)} 条 → 预排序池 {len(pool)} 条（{'自定义主题不使用效率词表过滤' if custom_topic else '词元分数≥'+str(a.min_score)}，池外 {n_below} 条）")
    print(f"  top {len(out)} 富集（摘要缺失 {missing}）→ {a.out}")
    print(f"  尾部抽样 {len(tail)}/{len(tail_pool)}（种子 {a.seed}）→ {a.out_tail}")

if __name__ == "__main__":
    main()
