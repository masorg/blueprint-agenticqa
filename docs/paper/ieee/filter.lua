-- Pandoc filter: turns the Markdown draft into IEEEtran-friendly LaTeX.
--  * headings lose their hand-typed numbers (IEEEtran numbers them); unnumbered ones are marked
--  * "**Table N.** caption" + a pipe table becomes a real table/table* with the caption inside
--  * images become figures that use the vector PDF next to each PNG
--  * horizontal rules are dropped

local function latex(inlines)
  local s = pandoc.write(pandoc.Pandoc({ pandoc.Plain(inlines) }), "latex")
  return (s:gsub("%s+$", ""):gsub("\n", " "))
end

local function block_latex(blocks)
  local s = pandoc.write(pandoc.Pandoc(blocks), "latex")
  return (s:gsub("%s+$", ""):gsub("\n+", " "))
end

local function strip_number(h)
  local first = h.content[1]
  if first and first.t == "Str" and first.text:match("^%d+[%.%d]*%.?$") then
    table.remove(h.content, 1)
    if h.content[1] and h.content[1].t == "Space" then table.remove(h.content, 1) end
    return true
  end
  return false
end

local function table_latex(tbl, caption)
  local ncols = #tbl.colspecs
  local nrows = #tbl.head.rows
  for _, body in ipairs(tbl.bodies) do nrows = nrows + #body.body end
  local chars = 0
  local function count(row) for _, c in ipairs(row.cells) do chars = chars + #pandoc.utils.stringify(c.contents) end end
  for _, r in ipairs(tbl.head.rows) do count(r) end
  for _, body in ipairs(tbl.bodies) do for _, r in ipairs(body.body) do count(r) end end
  local wide = ncols >= 5 or chars > 550  -- big tables span both columns
  local env = wide and "table*" or "table"
  local width = wide and "\\textwidth" or "\\columnwidth"
  local spec = {}
  for i = 1, ncols do spec[i] = ">{\\raggedright\\arraybackslash}X" end
  local first = pandoc.utils.stringify(tbl.head.rows[1].cells[1].contents)
  if ncols >= 3 and (first == "" or first == "#") then spec[1] = "l" end  -- a label or number column stays narrow
  local out = {}
  if not caption then env = "center" end  -- no caption: keep the table next to its text
  out[#out + 1] = (caption and ("\\begin{" .. env .. "}[t]")) or "\\begin{center}"
  if caption then out[#out + 1] = "\\caption{" .. caption .. "}" end
  out[#out + 1] = "\\centering" .. (wide and "\\scriptsize" or "\\footnotesize")
  out[#out + 1] = "\\begin{tabularx}{" .. width .. "}{" .. table.concat(spec) .. "}"
  out[#out + 1] = "\\toprule"
  local function row_latex(row, bold)
    local cells = {}
    for _, c in ipairs(row.cells) do
      local t = block_latex(c.contents)
      if bold and t ~= "" then t = "\\textbf{" .. t .. "}" end
      cells[#cells + 1] = t
    end
    return table.concat(cells, " & ") .. " \\\\"
  end
  for _, r in ipairs(tbl.head.rows) do out[#out + 1] = row_latex(r, true) end
  out[#out + 1] = "\\midrule"
  for _, body in ipairs(tbl.bodies) do
    for _, r in ipairs(body.body) do out[#out + 1] = row_latex(r, false) end
  end
  out[#out + 1] = "\\bottomrule"
  out[#out + 1] = "\\end{tabularx}"
  out[#out + 1] = "\\end{" .. env .. "}"
  return table.concat(out, "\n")
end

function Header(h)
  if not strip_number(h) then h.classes:insert("unnumbered") end
  return h
end

function HorizontalRule() return {} end

function Figure(fig)
  local img
  pandoc.walk_block(fig, { Image = function(i) img = i end })
  if not img then return nil end
  local path = img.src:gsub("^%.%./figures/", "../../figures/"):gsub("%.png$", ".pdf")
  local cap = fig.caption.long and block_latex(fig.caption.long) or ""
  cap = cap:gsub("^Figure %d+%.%s*", "")
  local wide = img.src:match("f6_injection") ~= nil or img.src:match("f0_framework") ~= nil
  local env = wide and "figure*" or "figure"
  local width = wide and "0.9\\textwidth" or "\\columnwidth"
  return pandoc.RawBlock("latex",
    "\\begin{" .. env .. "}[t]\\centering\\includegraphics[width=" .. width .. "]{" .. path .. "}\\caption{" .. cap .. "}\\end{" .. env .. "}")
end

function Pandoc(doc)
  local out, i = {}, 1
  local blocks = doc.blocks
  while i <= #blocks do
    local b = blocks[i]
    local nxt = blocks[i + 1]
    local is_cap = b.t == "Para" and b.content[1] and b.content[1].t == "Strong"
        and pandoc.utils.stringify(b.content[1]):match("^Table [%dA]*%d%.$")
    if is_cap then
      local rest = {}
      for k = 2, #b.content do rest[#rest + 1] = b.content[k] end
      if rest[1] and rest[1].t == "Space" then table.remove(rest, 1) end
      local cap = latex(rest)
      -- the caption may be followed by the table directly, or by a placeholder paragraph
      if nxt and nxt.t == "Table" then
        out[#out + 1] = pandoc.RawBlock("latex", table_latex(nxt, cap))
        i = i + 2
      else
        out[#out + 1] = b
        i = i + 1
      end
    elseif b.t == "Table" then  -- a table without a caption line (the layers list in Section 3.1)
      out[#out + 1] = pandoc.RawBlock("latex", table_latex(b, nil))
      i = i + 1
    else
      out[#out + 1] = b
      i = i + 1
    end
  end
  doc.blocks = out
  return doc
end
