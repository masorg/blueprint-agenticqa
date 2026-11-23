import re, sys
from pathlib import Path
P = Path("/tmp/paper")
repo = Path("/Users/msaleem/work/cloudera/agentic-qa")
cur = (repo / "docs/paper/draft.template.md").read_text()
head = cur[: cur.index("## Abstract")] if "## Abstract" in cur else cur[: cur.index("## 1.")]
front = (P / "front_v3.md").read_text()          # abstract + introduction
rw = (P / "rw_v2.md").read_text()
sec3 = (P / "sec3_v2.md").read_text()
sec3 = sec3.replace("(Table 3)", "(Table 2)").replace("**Table 3.** The layers", "**Table 2.** The layers")
sec4 = (P / "tail_v2.md").read_text()
res12 = (P / "res12.md").read_text()
rest = (P / "res_tail_v2.md").read_text()
rest = rest.replace("**Table 4.** How often", "**Table 5.** How often").replace("**Table 5.** Share of the", "**Table 6.** Share of the")
refs_old = (P / "rest.md").read_text()
refs_old = refs_old[refs_old.index("## References"):]
new_refs = '''
[23] G. Rothermel and M. J. Harrold, "A safe, efficient regression test selection technique," ACM Trans. Softw. Eng. Methodol., vol. 6, no. 2, 1997, doi:10.1145/248233.248262.
[24] M. Gligoric, L. Eloussi, and D. Marinov, "Practical regression test selection with dynamic file dependencies," in Proc. ISSTA, 2015, doi:10.1145/2771783.2771784.
[25] O. Legunsen, F. Hariri, A. Shi, Y. Lu, L. Zhang, and D. Marinov, "An extensive study of static regression test selection in modern software evolution," in Proc. FSE, 2016, doi:10.1145/2950290.2950361.
[26] M. Machalica, A. Samylkin, M. Porth, and S. Chandra, "Predictive Test Selection," arXiv:1810.05286, 2018 (camera-ready 2019).
[27] A. Memon et al., "Taming Google-scale continuous testing," in Proc. ICSE-SEIP, 2017, doi:10.1109/ICSE-SEIP.2017.16.
[28] Y. Wang, M. Pradel, and Z. Liu, "Names Are All You Need: Effective and Safe Regression Test Selection for Python," arXiv:2605.25356, 2026 (accepted at ISSTA 2026).
[29] pytest-testmon, https://github.com/tarpas/pytest-testmon, accessed 2026-10-23.
[30] P. Alonso, S. Yovine, and V. A. Braberman, "TDAD: Test-Driven Agentic Development - Reducing Code Regressions in AI Coding Agents via Graph-Based Impact Analysis," arXiv:2603.17973, 2026.
[32] C. E. Jimenez, J. Yang, A. Wettig, S. Yao, K. Pei, O. Press, and K. Narasimhan, "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" in Proc. ICLR, 2024, arXiv:2310.06770. The Verified subset (500 human-validated instances) is hosted at huggingface.co/datasets/princeton-nlp/SWE-bench_Verified.
[31] B. A. Muse, L. Briand, Y. Lu, and K. Gallaba, "CASPER-Change-Aware Slice Prioritization for Efficient Regression Testing of LLM-based systems," arXiv:2608.00378, 2026.
'''
# insert new refs after [22] line block
m = re.search(r"^\[22\] .*$", refs_old, re.M)
refs_old = refs_old[: m.end()] + "\n" + new_refs.strip("\n") + refs_old[m.end():]
# re-check statement about reading depth: drop obsolete author note about [7]/[8] partial reading
refs_old = re.sub(r"\n\*\[AUTHOR: the related-work statements.*?\]\*", "", refs_old, flags=re.S)
full = head + front + rw + sec3.rstrip() + "\n\n---\n\n" + sec4 + res12 + rest + refs_old

keep = "@@ALG@@"
full = full.replace("\\begin{algorithmic}[1]", keep)
ri = full.index("## References")
body, refs = full[:ri], full[ri:]
reflines = [l for l in refs.splitlines() if re.match(r"^\[\d+\] ", l)]
refmap = {int(re.match(r"^\[(\d+)\]", l).group(1)): l for l in reflines}
pat = re.compile(r"\[(\d+(?:\s*(?:,|to)\s*\d+)*)\]")
def expand(g):
    out = []
    for tok in re.split(r"\s*,\s*", g):
        m = re.match(r"^(\d+)\s*to\s*(\d+)$", tok)
        out += list(range(int(m.group(1)), int(m.group(2)) + 1)) if m else [int(tok)]
    return out
order = []
unknown = set()
for m in pat.finditer(body):
    for n in expand(m.group(1)):
        if n in refmap:
            if n not in order: order.append(n)
        else:
            unknown.add(n)
missing = [n for n in refmap if n not in order]
newno = {old: i + 1 for i, old in enumerate(order + missing)}
def fmt(nums):
    nums = sorted(set(nums)); out = []; i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1: j += 1
        out += [f"{nums[i]} to {nums[j]}"] if j - i >= 2 else [str(x) for x in nums[i:j + 1]]
        i = j + 1
    return "[" + ", ".join(out) + "]"
body = pat.sub(lambda m: fmt([newno[n] for n in expand(m.group(1)) if n in newno]) if any(n in newno for n in expand(m.group(1))) else m.group(0), body)
newrefs = "## References\n\n" + "\n".join(re.sub(r"^\[\d+\]", "[%d]" % newno[o], refmap[o]) for o in order + missing) + "\n"
out = (body + newrefs).replace(keep, "\\begin{algorithmic}[1]")
(repo / "docs/paper/draft.template.md").write_text(out)
print("uncited:", missing, "unknown:", sorted(unknown))
