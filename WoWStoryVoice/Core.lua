local addonName = ...
local WSV = CreateFrame("Frame")
local pixels = {}
local seq = 0
local MAGIC = "WSV4"
local MAX_PAYLOAD = 96
local PIXEL_SIZE = 5
local X0, Y0 = 20, -20

local function checksum(s)
  local c = 0
  for i = 1, #s do c = (c + string.byte(s, i)) % 256 end
  return c
end

local function utf8SafePrefix(s, maxBytes)
  if #s <= maxBytes then return s end
  local cut = maxBytes
  while cut > 0 do
    local b = string.byte(s, cut)
    if b < 128 then return string.sub(s, 1, cut) end
    if b >= 194 then
      local need = (b < 224 and 2) or (b < 240 and 3) or 4
      if cut + need - 1 <= maxBytes then return string.sub(s, 1, cut + need - 1) end
      return string.sub(s, 1, cut - 1)
    end
    cut = cut - 1
  end
  return ""
end

local function ensurePixels(n)
  for i = #pixels + 1, n do
    local t = UIParent:CreateTexture(nil, "OVERLAY")
    t:SetSize(PIXEL_SIZE, PIXEL_SIZE)
    t:SetPoint("TOPLEFT", UIParent, "TOPLEFT", X0 + (i-1)*PIXEL_SIZE, Y0)
    t:SetColorTexture(0,0,0,1)
    pixels[i] = t
  end
end

-- Robust transport: each byte is represented by two 4-bit grayscale cells.
-- 16 levels are 17 luminance values apart, giving the screen capture ample
-- tolerance instead of requiring an exact 0..255 grayscale byte.
local function emitByteCells(bytes)
  local cells = {}
  for i = 1, #bytes do
    local b = string.byte(bytes, i)
    cells[#cells+1] = math.floor(b / 16)
    cells[#cells+1] = b % 16
  end
  ensurePixels(#cells)
  for i = 1, #pixels do
    if i <= #cells then
      local v = cells[i] / 15
      pixels[i]:SetColorTexture(v,v,v,1)
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
  local packet = MAGIC .. string.char(seq) .. string.char(#payload) .. payload .. string.char(checksum(payload))
  emitByteCells(packet)
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
    if text and text ~= "" then emit("gossip", npcName(), text) end
  end
end)

SLASH_WOWSTORYVOICE1 = "/wsv"
SlashCmdList.WOWSTORYVOICE = function(msg)
  msg = string.lower(msg or "")
  if msg == "test" then
    emit("test", "Narrator", "WoW Story Voice is connected and ready.")
    print("|cff66ff66WoW Story Voice:|r test packet sent (v0.4 transport).")
  elseif msg == "hide" then
    for _, p in ipairs(pixels) do p:Hide() end
  elseif msg == "show" then
    emit("test", "Narrator", "Bridge visible.")
  else
    print("|cff66ff66WoW Story Voice|r: /wsv test, /wsv show, /wsv hide")
  end
end
