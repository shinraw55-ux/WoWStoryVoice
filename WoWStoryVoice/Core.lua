local addonName = ...
local WSV = CreateFrame("Frame")
local pixels = {}
local seq = 0

local VERSION = "0.5.0"
local MAGIC = "WSV5"
local MAX_PAYLOAD = 96

-- Desired physical pixel geometry. We convert physical pixels to UI units
-- using Blizzard's own PixelUtil formula so UI Scale cannot stretch the
-- transport unpredictably.
local CELL_PX = 5
local X_PX = 20
local Y_PX = 20
local CELLS_PER_BYTE = 3

local function checksum(s)
  local c = 0
  for i = 1, #s do
    c = (c + string.byte(s, i)) % 256
  end
  return c
end

local function utf8SafePrefix(s, maxBytes)
  if #s <= maxBytes then return s end
  local cut = maxBytes
  while cut > 0 do
    local b = string.byte(s, cut)
    if b < 128 then
      return string.sub(s, 1, cut)
    end
    if b >= 194 then
      local need = (b < 224 and 2) or (b < 240 and 3) or 4
      if cut + need - 1 <= maxBytes then
        return string.sub(s, 1, cut + need - 1)
      end
      return string.sub(s, 1, cut - 1)
    end
    cut = cut - 1
  end
  return ""
end

local function pixelsToUI(px, region)
  local factor = PixelUtil.GetPixelToUIUnitFactor()
  return px * factor / region:GetEffectiveScale()
end

local function ensurePixels(n)
  for i = #pixels + 1, n do
    local t = UIParent:CreateTexture(nil, "OVERLAY")
    local size = pixelsToUI(CELL_PX, t)
    local x = pixelsToUI(X_PX + (i - 1) * CELL_PX, t)
    local y = pixelsToUI(Y_PX, t)

    t:SetSize(size, size)
    t:SetPoint("TOPLEFT", UIParent, "TOPLEFT", x, -y)
    t:SetSnapToPixelGrid(true)
    t:SetColorTexture(0, 0, 0, 1)
    pixels[i] = t
  end
end

local function appendByteCells(cells, value)
  -- Three RGB cells carry one byte:
  -- cell 1 = bits 7,6,5; cell 2 = bits 4,3,2; cell 3 = bits 1,0,padding.
  -- Channels use only 0 or 1, avoiding grayscale/gamma ambiguity.
  local bits = {}
  for shift = 7, 0, -1 do
    bits[#bits + 1] = math.floor(value / (2 ^ shift)) % 2
  end
  bits[#bits + 1] = 0

  for i = 1, 9, 3 do
    cells[#cells + 1] = { bits[i], bits[i + 1], bits[i + 2] }
  end
end

local function emitBytes(bytes)
  local cells = {}
  for i = 1, #bytes do
    appendByteCells(cells, string.byte(bytes, i))
  end

  ensurePixels(#cells)

  for i = 1, #pixels do
    if i <= #cells then
      local c = cells[i]
      pixels[i]:SetColorTexture(c[1], c[2], c[3], 1)
      pixels[i]:Show()
    else
      pixels[i]:Hide()
    end
  end
end

local function emit(kind, npc, text)
  npc = npc or "Unknown"
  text = text or ""

  local prefix = kind .. "\31" .. npc .. "\31"
  if #prefix >= MAX_PAYLOAD then
    npc = utf8SafePrefix(npc, math.max(1, MAX_PAYLOAD - #kind - 3))
    prefix = kind .. "\31" .. npc .. "\31"
  end

  local payload = prefix .. utf8SafePrefix(text, math.max(0, MAX_PAYLOAD - #prefix))
  seq = (seq + 1) % 256

  local packet =
    MAGIC ..
    string.char(seq) ..
    string.char(#payload) ..
    payload ..
    string.char(checksum(payload))

  emitBytes(packet)
end

local function npcName()
  return UnitName("npc") or UnitName("target") or "Narrator"
end

WSV:RegisterEvent("QUEST_DETAIL")
WSV:RegisterEvent("QUEST_COMPLETE")
WSV:RegisterEvent("GOSSIP_SHOW")

WSV:SetScript("OnEvent", function(_, event)
  if event == "QUEST_DETAIL" then
    emit("quest", npcName(), GetQuestText())
  elseif event == "QUEST_COMPLETE" then
    emit("reward", npcName(), GetRewardText())
  elseif event == "GOSSIP_SHOW" then
    local text = C_GossipInfo and C_GossipInfo.GetText and C_GossipInfo.GetText()
    if text and text ~= "" then
      emit("gossip", npcName(), text)
    end
  end
end)

SLASH_WOWSTORYVOICE1 = "/wsv"
SlashCmdList.WOWSTORYVOICE = function(msg)
  msg = string.lower(msg or "")

  if msg == "test" then
    emit("test", "Narrator", "WoW Story Voice is connected and ready.")
    print("|cff66ff66WoW Story Voice:|r test packet sent (v" .. VERSION .. ", WSV5 RGB transport).")
  elseif msg == "hide" then
    for _, p in ipairs(pixels) do
      p:Hide()
    end
  elseif msg == "show" then
    emit("test", "Narrator", "Bridge visible.")
  else
    print("|cff66ff66WoW Story Voice " .. VERSION .. "|r: /wsv test, /wsv show, /wsv hide")
  end
end
