local addonName = ...
local WSV = CreateFrame("Frame")
local pixels = {}

local VERSION = "0.7.0"
local MAGIC = "WSV6"

-- WSV6 transport remains unchanged from v0.6.0 to protect the live-verified
-- RGB bridge while higher-level dialogue handling evolves.
local CELL_PX = 5
local X_PX = 20
local Y_PX = 20

local CHUNK_DATA_MAX = 91
local TX_HOLD_SEC = 0.16
local TX_MIN_WINDOW_SEC = 2.6
local TX_MIN_ROUNDS = 2

local messageId = 0
local txQueue = {}
local txHead = 1
local txElapsed = 0
local currentPacket = nil

WoWStoryVoiceDB = WoWStoryVoiceDB or {}
if WoWStoryVoiceDB.skipBlizzardVoiced == nil then
  WoWStoryVoiceDB.skipBlizzardVoiced = true
end
if WoWStoryVoiceDB.monsterDialogue == nil then
  WoWStoryVoiceDB.monsterDialogue = true
end

local MONSTER_EVENT_KIND = {
  CHAT_MSG_MONSTER_SAY = "monster_say",
  CHAT_MSG_MONSTER_YELL = "monster_yell",
  CHAT_MSG_MONSTER_WHISPER = "monster_whisper",
  CHAT_MSG_MONSTER_PARTY = "monster_party",
}

local function checksum(s)
  local c = 0
  for i = 1, #s do
    c = (c + string.byte(s, i)) % 256
  end
  return c
end

local function u16Bytes(n)
  return string.char(math.floor(n / 256) % 256, n % 256)
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

local function makeChunkPacket(msgId, chunkIndex, chunkTotal, chunkData)
  local meta =
    u16Bytes(msgId) ..
    u16Bytes(chunkIndex) ..
    u16Bytes(chunkTotal) ..
    string.char(#chunkData)

  local body = meta .. chunkData
  return MAGIC .. body .. string.char(checksum(body))
end

local function compactTxQueue()
  if txHead <= 128 then return end
  local remaining = {}
  for i = txHead, #txQueue do
    remaining[#remaining + 1] = txQueue[i]
  end
  txQueue = remaining
  txHead = 1
end

local function clearTxQueue()
  txQueue = {}
  txHead = 1
  txElapsed = 0
  currentPacket = nil
end

local function enqueueMessage(kind, npcGuid, npc, text, priority)
  kind = kind or "unknown"
  npcGuid = npcGuid or ""
  npc = npc or "Unknown"
  text = text or ""

  local message = kind .. "\31" .. npcGuid .. "\31" .. npc .. "\31" .. text
  local chunkTotal = math.max(1, math.ceil(#message / CHUNK_DATA_MAX))

  if chunkTotal > 65535 then
    print("|cffff6666WoW Story Voice:|r message is too large to transmit.")
    return
  end

  messageId = (messageId + 1) % 65536
  local packets = {}

  for chunkIndex = 1, chunkTotal do
    local startByte = (chunkIndex - 1) * CHUNK_DATA_MAX + 1
    local chunkData = string.sub(message, startByte, startByte + CHUNK_DATA_MAX - 1)
    packets[#packets + 1] = makeChunkPacket(messageId, chunkIndex, chunkTotal, chunkData)
  end

  local cycleSec = #packets * TX_HOLD_SEC
  local rounds = math.max(TX_MIN_ROUNDS, math.ceil(TX_MIN_WINDOW_SEC / math.max(cycleSec, TX_HOLD_SEC)))
  rounds = math.min(rounds, 16)

  local staged = {}
  for _ = 1, rounds do
    for _, packet in ipairs(packets) do
      staged[#staged + 1] = packet
    end
  end

  if priority then
    local oldRemaining = {}
    for i = txHead, #txQueue do
      oldRemaining[#oldRemaining + 1] = txQueue[i]
    end
    txQueue = staged
    for _, packet in ipairs(oldRemaining) do
      txQueue[#txQueue + 1] = packet
    end
    txHead = 1
    txElapsed = 0
    currentPacket = nil
  else
    for _, packet in ipairs(staged) do
      txQueue[#txQueue + 1] = packet
    end
  end
end

WSV:SetScript("OnUpdate", function(_, elapsed)
  if txHead > #txQueue then
    compactTxQueue()
    return
  end

  txElapsed = txElapsed + elapsed
  if currentPacket and txElapsed < TX_HOLD_SEC then
    return
  end

  currentPacket = txQueue[txHead]
  txHead = txHead + 1
  txElapsed = 0
  emitBytes(currentPacket)
end)

local function npcInfo()
  local unit = nil
  if UnitName("npc") then
    unit = "npc"
  elseif UnitName("target") then
    unit = "target"
  end

  if not unit then
    return "", "Narrator"
  end

  return UnitGUID(unit) or "", UnitName(unit) or "Narrator"
end

local function capture(kind, text)
  if type(text) ~= "string" or text == "" then return end
  local guid, name = npcInfo()
  enqueueMessage(kind, guid, name, text, false)
end

local function captureMonsterEvent(event, ...)
  if not WoWStoryVoiceDB.monsterDialogue then return end

  local text = select(1, ...)
  local name = select(2, ...)
  local guid = select(12, ...)
  local isSubtitle = select(15, ...)
  local hideSenderInLetterbox = select(16, ...)

  if type(text) ~= "string" or text == "" then return end
  if type(name) ~= "string" or name == "" then name = "Unknown" end
  if type(guid) ~= "string" then guid = "" end

  -- Blizzard marks chat lines used as subtitles/cinematic text in the generic
  -- CHAT_MSG payload. With the default setting enabled, do not synthesize those
  -- lines so the local TTS does not speak over Blizzard's original presentation.
  if WoWStoryVoiceDB.skipBlizzardVoiced and (isSubtitle == true or hideSenderInLetterbox == true) then
    return
  end

  enqueueMessage(MONSTER_EVENT_KIND[event] or "monster", guid, name, text, false)
end

WSV:RegisterEvent("QUEST_DETAIL")
WSV:RegisterEvent("QUEST_PROGRESS")
WSV:RegisterEvent("QUEST_COMPLETE")
WSV:RegisterEvent("QUEST_GREETING")
WSV:RegisterEvent("GOSSIP_SHOW")
WSV:RegisterEvent("CHAT_MSG_MONSTER_SAY")
WSV:RegisterEvent("CHAT_MSG_MONSTER_YELL")
WSV:RegisterEvent("CHAT_MSG_MONSTER_WHISPER")
WSV:RegisterEvent("CHAT_MSG_MONSTER_PARTY")

WSV:SetScript("OnEvent", function(_, event, ...)
  if event == "QUEST_DETAIL" then
    capture("quest", GetQuestText())
  elseif event == "QUEST_PROGRESS" then
    capture("progress", GetProgressText())
  elseif event == "QUEST_COMPLETE" then
    capture("reward", GetRewardText())
  elseif event == "QUEST_GREETING" then
    capture("greeting", GetGreetingText())
  elseif event == "GOSSIP_SHOW" then
    local text = C_GossipInfo and C_GossipInfo.GetText and C_GossipInfo.GetText()
    capture("gossip", text)
  elseif MONSTER_EVENT_KIND[event] then
    captureMonsterEvent(event, ...)
  end
end)

local function boolText(value)
  return value and "ON" or "OFF"
end

SLASH_WOWSTORYVOICE1 = "/wsv"
SlashCmdList.WOWSTORYVOICE = function(msg)
  local raw = msg or ""
  local command, arg = string.match(string.lower(raw), "^(%S*)%s*(%S*)")

  if command == "test" then
    enqueueMessage(
      "test",
      "",
      "Narrator",
      "WoW Story Voice is connected and ready. Full dialogue, queued speech, persistent NPC voices and improved pacing are active.",
      true
    )
    print("|cff66ff66WoW Story Voice:|r test queued (v" .. VERSION .. ", WSV6 transport).")
  elseif command == "stop" then
    clearTxQueue()
    enqueueMessage("control", "", "Narrator", "stop", true)
    print("|cff66ff66WoW Story Voice:|r stop command sent.")
  elseif command == "blizzard" and (arg == "on" or arg == "off") then
    WoWStoryVoiceDB.skipBlizzardVoiced = (arg == "on")
    print("|cff66ff66WoW Story Voice:|r skip Blizzard subtitle/cinematic lines: " .. boolText(WoWStoryVoiceDB.skipBlizzardVoiced))
  elseif command == "monsters" and (arg == "on" or arg == "off") then
    WoWStoryVoiceDB.monsterDialogue = (arg == "on")
    print("|cff66ff66WoW Story Voice:|r ambient NPC dialogue: " .. boolText(WoWStoryVoiceDB.monsterDialogue))
  elseif command == "status" then
    print("|cff66ff66WoW Story Voice " .. VERSION .. "|r / WSV6")
    print("  Skip Blizzard subtitle/cinematic lines: " .. boolText(WoWStoryVoiceDB.skipBlizzardVoiced))
    print("  Ambient NPC dialogue: " .. boolText(WoWStoryVoiceDB.monsterDialogue))
  elseif command == "hide" then
    for _, p in ipairs(pixels) do
      p:Hide()
    end
  elseif command == "show" then
    enqueueMessage("test", "", "Narrator", "Bridge visible.", true)
  else
    print("|cff66ff66WoW Story Voice " .. VERSION .. "|r commands:")
    print("  /wsv test, /wsv stop, /wsv status, /wsv show, /wsv hide")
    print("  /wsv blizzard on|off, /wsv monsters on|off")
  end
end
