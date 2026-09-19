local addonName = ...
local WSV = CreateFrame("Frame")
local pixels = {}

local VERSION = "0.8.0"
local MAGIC = "WSV6"

-- Keep WSV6 framing unchanged. Latency tuning only changes when already-built
-- packets are scheduled: fresh dialogue gets one complete pass immediately,
-- while redundant reliability passes are sent only when no fresh data waits.
local CELL_PX = 5
local X_PX = 20
local Y_PX = 20
local CHUNK_DATA_MAX = 91
local TX_HOLD_SEC = 0.12
local TX_MIN_WINDOW_SEC = 2.6
local TX_MIN_ROUNDS = 2
local HEARTBEAT_SEC = 30

local messageId = 0
local txQueue = {}
local txHead = 1
local retryQueue = {}
local retryHead = 1
local txElapsed = 0
local currentPacket = nil
local heartbeatElapsed = HEARTBEAT_SEC
local optionsFrame = nil
local optionChecks = {}
local npcProfileCache = {}
local currentInteractionType = nil

WoWStoryVoiceDB = WoWStoryVoiceDB or {}
if WoWStoryVoiceDB.skipBlizzardVoiced == nil then WoWStoryVoiceDB.skipBlizzardVoiced = true end
if WoWStoryVoiceDB.monsterDialogue == nil then WoWStoryVoiceDB.monsterDialogue = true end
if WoWStoryVoiceDB.questDialogue == nil then WoWStoryVoiceDB.questDialogue = true end
if WoWStoryVoiceDB.gossipDialogue == nil then WoWStoryVoiceDB.gossipDialogue = true end
if type(WoWStoryVoiceDB.npcProfiles) ~= "table" then WoWStoryVoiceDB.npcProfiles = {} end

local INTERACTION_ROLE = {
  [5] = "merchant",
  [7] = "trainer",
  [8] = "banker",
  [10] = "banker",
  [18] = "spirithealer",
  [21] = "auctioneer",
  [22] = "stablemaster",
  [23] = "battlemaster",
  [24] = "transmogrifier",
  [27] = "auctioneer",
  [52] = "guide",
  [57] = "merchant",
  [59] = "profession",
  [66] = "forgemaster",
  [67] = "banker",
  [68] = "banker",
}

local MONSTER_EVENT_KIND = {
  CHAT_MSG_MONSTER_SAY = "monster_say",
  CHAT_MSG_MONSTER_YELL = "monster_yell",
  CHAT_MSG_MONSTER_WHISPER = "monster_whisper",
  CHAT_MSG_MONSTER_PARTY = "monster_party",
}

local function checksum(s)
  local c = 0
  for i = 1, #s do c = (c + string.byte(s, i)) % 256 end
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
  for shift = 7, 0, -1 do bits[#bits + 1] = math.floor(value / (2 ^ shift)) % 2 end
  bits[#bits + 1] = 0
  for i = 1, 9, 3 do cells[#cells + 1] = { bits[i], bits[i + 1], bits[i + 2] } end
end

local function emitBytes(bytes)
  local cells = {}
  for i = 1, #bytes do appendByteCells(cells, string.byte(bytes, i)) end
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
  local meta = u16Bytes(msgId) .. u16Bytes(chunkIndex) .. u16Bytes(chunkTotal) .. string.char(#chunkData)
  local body = meta .. chunkData
  return MAGIC .. body .. string.char(checksum(body))
end

local function compactQueue(queue, head)
  if head <= 128 then return queue, head end
  local remaining = {}
  for i = head, #queue do remaining[#remaining + 1] = queue[i] end
  return remaining, 1
end

local function compactTxQueues()
  txQueue, txHead = compactQueue(txQueue, txHead)
  retryQueue, retryHead = compactQueue(retryQueue, retryHead)
end

local function clearTxQueue()
  txQueue = {}
  txHead = 1
  retryQueue = {}
  retryHead = 1
  txElapsed = 0
  currentPacket = nil
end

local function prependPackets(queue, head, packets)
  local merged = {}
  for _, packet in ipairs(packets) do merged[#merged + 1] = packet end
  for i = head, #queue do merged[#merged + 1] = queue[i] end
  return merged, 1
end

local function enqueueMessage(kind, npcGuid, npc, text, priority, quick)
  kind = kind or "unknown"
  npcGuid = npcGuid or ""
  npc = npc or "Unknown"
  text = text or ""

  -- Notify visual modules from the exact same accepted dialogue path as the transport.
  -- Strip the private profile suffix before exposing the GUID to UI modules.
  if kind ~= "control" and type(WoWStoryVoice_ActivateSpeaker) == "function" then
    local visualGuid = string.match(npcGuid, "^(.-)#wsv#") or npcGuid
    pcall(WoWStoryVoice_ActivateSpeaker, visualGuid, npc, text, kind)
  end

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

  local rounds
  if quick then
    rounds = 2
  else
    local cycleSec = #packets * TX_HOLD_SEC
    rounds = math.max(TX_MIN_ROUNDS, math.ceil(TX_MIN_WINDOW_SEC / math.max(cycleSec, TX_HOLD_SEC)))
    rounds = math.min(rounds, 16)
  end

  -- First pass is always the latency-sensitive pass. Redundant passes preserve
  -- one-way loss recovery, but live in a separate queue so a new line never
  -- waits behind seconds of repeats from an older line.
  if priority then
    txQueue, txHead = prependPackets(txQueue, txHead, packets)
    txElapsed = 0
    currentPacket = nil
  else
    for _, packet in ipairs(packets) do txQueue[#txQueue + 1] = packet end
  end

  for _ = 2, rounds do
    for _, packet in ipairs(packets) do retryQueue[#retryQueue + 1] = packet end
  end
end

local function queueHeartbeat()
  enqueueMessage("control", "", "Narrator", "hello|" .. VERSION, false, true)
end

local function hasFreshPackets()
  return txHead <= #txQueue
end

local function hasRetryPackets()
  return retryHead <= #retryQueue
end

local function nextPacket()
  if hasFreshPackets() then
    local packet = txQueue[txHead]
    txHead = txHead + 1
    return packet
  end
  if hasRetryPackets() then
    local packet = retryQueue[retryHead]
    retryHead = retryHead + 1
    return packet
  end
  return nil
end

WSV:SetScript("OnUpdate", function(_, elapsed)
  heartbeatElapsed = heartbeatElapsed + elapsed
  if heartbeatElapsed >= HEARTBEAT_SEC and not hasFreshPackets() and not hasRetryPackets() then
    heartbeatElapsed = 0
    queueHeartbeat()
  end

  if not hasFreshPackets() and not hasRetryPackets() then
    compactTxQueues()
    return
  end

  txElapsed = txElapsed + elapsed
  if currentPacket and txElapsed < TX_HOLD_SEC then return end
  currentPacket = nextPacket()
  if not currentPacket then return end
  txElapsed = 0
  emitBytes(currentPacket)
end)

local function sanitizeProfileValue(value)
  local text = tostring(value or "")
  return (string.gsub(text, "[^%w_%-]", "_"))
end

local function guidTemplateKey(guid)
  if type(guid) ~= "string" or guid == "" then return "" end
  local parts = {}
  for part in string.gmatch(guid, "([^-]+)") do
    parts[#parts + 1] = part
    if #parts >= 6 then break end
  end
  if #parts >= 6 and (parts[1] == "Creature" or parts[1] == "Vehicle") then
    return parts[1] .. ":" .. parts[6]
  end
  return guid
end

local function profileSuffixForUnit(unit)
  local sex = tonumber(UnitSex(unit)) or 1
  local _, englishRace = UnitRace(unit)
  local _, creatureTypeID = UnitCreatureType(unit)
  local _, classFile = UnitClass(unit)
  local classification = UnitClassification(unit)
  local role = ""
  if unit == "npc" and currentInteractionType ~= nil then
    role = INTERACTION_ROLE[tonumber(currentInteractionType)] or ""
  end
  return "#wsv#sex=" .. tostring(sex)
    .. ";race=" .. sanitizeProfileValue(englishRace)
    .. ";ctype=" .. sanitizeProfileValue(creatureTypeID)
    .. ";class=" .. sanitizeProfileValue(classFile)
    .. ";rank=" .. sanitizeProfileValue(classification)
    .. ";role=" .. sanitizeProfileValue(role)
end

local function rememberProfile(guid, suffix)
  if type(guid) ~= "string" or guid == "" or type(suffix) ~= "string" then return end
  npcProfileCache[guid] = suffix
  local templateKey = guidTemplateKey(guid)
  if templateKey ~= "" then
    npcProfileCache[templateKey] = suffix
    WoWStoryVoiceDB.npcProfiles[templateKey] = suffix
  end
end

local function profiledGuidForUnit(unit)
  local guid = UnitGUID(unit) or ""
  if guid == "" then return "" end
  local suffix = profileSuffixForUnit(unit)
  rememberProfile(guid, suffix)
  return guid .. suffix
end

local function profiledGuidForSender(guid)
  if type(guid) ~= "string" or guid == "" then return "" end

  -- Ambient chat only gives us a GUID, not a unit token. If the speaker is a
  -- currently addressable NPC/target/focus/mouseover, learn its live profile.
  local units = { "npc", "target", "focus", "mouseover" }
  for _, unit in ipairs(units) do
    if UnitGUID(unit) == guid then
      return profiledGuidForUnit(unit)
    end
  end

  local templateKey = guidTemplateKey(guid)
  local suffix = npcProfileCache[guid] or npcProfileCache[templateKey] or WoWStoryVoiceDB.npcProfiles[templateKey]
  if suffix then
    npcProfileCache[guid] = suffix
    if templateKey ~= "" then npcProfileCache[templateKey] = suffix end
    return guid .. suffix
  end
  return guid
end

local function npcInfo()
  local unit = nil
  if UnitName("npc") then unit = "npc" elseif UnitName("target") then unit = "target" end
  if not unit then return "", "Narrator" end
  return profiledGuidForUnit(unit), UnitName(unit) or "Narrator"
end

local function capture(kind, text)
  if type(text) ~= "string" or text == "" then return end
  local guid, name = npcInfo()
  enqueueMessage(kind, guid, name, text, false, false)
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
  if WoWStoryVoiceDB.skipBlizzardVoiced and (isSubtitle == true or hideSenderInLetterbox == true) then return end
  guid = profiledGuidForSender(guid)
  enqueueMessage(MONSTER_EVENT_KIND[event] or "monster", guid, name, text, false, false)
end

local function boolText(value)
  return value and "ON" or "OFF"
end

local function printStatus()
  print("|cff66ff66WoW Story Voice " .. VERSION .. "|r / WSV6")
  print("  Quest dialogue: " .. boolText(WoWStoryVoiceDB.questDialogue))
  print("  Gossip dialogue: " .. boolText(WoWStoryVoiceDB.gossipDialogue))
  print("  Ambient NPC dialogue: " .. boolText(WoWStoryVoiceDB.monsterDialogue))
  print("  Skip Blizzard subtitle/cinematic lines: " .. boolText(WoWStoryVoiceDB.skipBlizzardVoiced))
  print("  NPC speaker indicator: " .. boolText(WoWStoryVoiceDB.speakerIndicator ~= false))
end

local function refreshOptionChecks()
  if not optionsFrame then return end
  if optionChecks.quest then optionChecks.quest:SetChecked(WoWStoryVoiceDB.questDialogue) end
  if optionChecks.gossip then optionChecks.gossip:SetChecked(WoWStoryVoiceDB.gossipDialogue) end
  if optionChecks.monsters then optionChecks.monsters:SetChecked(WoWStoryVoiceDB.monsterDialogue) end
  if optionChecks.blizzard then optionChecks.blizzard:SetChecked(WoWStoryVoiceDB.skipBlizzardVoiced) end
  if optionChecks.indicator then optionChecks.indicator:SetChecked(WoWStoryVoiceDB.speakerIndicator ~= false) end
end

local function makeCheck(parent, label, y, getter, setter)
  local check = CreateFrame("CheckButton", nil, parent, "UICheckButtonTemplate")
  check:SetPoint("TOPLEFT", 22, y)
  check:SetSize(26, 26)
  local text = parent:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  text:SetPoint("LEFT", check, "RIGHT", 6, 0)
  text:SetText(label)
  check:SetScript("OnClick", function(self)
    setter(self:GetChecked() and true or false)
  end)
  check:SetChecked(getter())
  return check
end

local function makeButton(parent, label, x, y, width, callback)
  local button = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
  button:SetSize(width or 105, 24)
  button:SetPoint("BOTTOMLEFT", x, y)
  button:SetText(label)
  button:SetScript("OnClick", callback)
  return button
end

local function createOptionsFrame()
  if optionsFrame then return optionsFrame end

  local frame = CreateFrame("Frame", "WoWStoryVoiceOptionsFrame", UIParent)
  frame:SetSize(450, 330)
  frame:SetPoint("CENTER")
  frame:SetFrameStrata("DIALOG")
  frame:SetMovable(true)
  frame:EnableMouse(true)
  frame:RegisterForDrag("LeftButton")
  frame:SetScript("OnDragStart", frame.StartMoving)
  frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
  frame:SetClampedToScreen(true)

  local bg = frame:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints(frame)
  bg:SetColorTexture(0.035, 0.035, 0.05, 0.96)

  local border = frame:CreateTexture(nil, "BORDER")
  border:SetPoint("TOPLEFT", -1, 1)
  border:SetPoint("BOTTOMRIGHT", 1, -1)
  border:SetColorTexture(0.25, 0.25, 0.30, 1)
  local inner = frame:CreateTexture(nil, "ARTWORK")
  inner:SetPoint("TOPLEFT", 1, -1)
  inner:SetPoint("BOTTOMRIGHT", -1, 1)
  inner:SetColorTexture(0.035, 0.035, 0.05, 1)

  local title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
  title:SetPoint("TOPLEFT", 20, -18)
  title:SetText("WoW Story Voice")

  local version = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  version:SetPoint("TOPLEFT", title, "BOTTOMLEFT", 0, -4)
  version:SetText("Addon v" .. VERSION .. " · WSV6")

  local close = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
  close:SetPoint("TOPRIGHT", -4, -4)

  optionChecks.quest = makeCheck(frame, "Read quest dialogue", -72,
    function() return WoWStoryVoiceDB.questDialogue end,
    function(v) WoWStoryVoiceDB.questDialogue = v end)
  optionChecks.gossip = makeCheck(frame, "Read gossip dialogue", -108,
    function() return WoWStoryVoiceDB.gossipDialogue end,
    function(v) WoWStoryVoiceDB.gossipDialogue = v end)
  optionChecks.monsters = makeCheck(frame, "Read ambient NPC speech", -144,
    function() return WoWStoryVoiceDB.monsterDialogue end,
    function(v) WoWStoryVoiceDB.monsterDialogue = v end)
  optionChecks.blizzard = makeCheck(frame, "Skip Blizzard subtitle/cinematic lines", -180,
    function() return WoWStoryVoiceDB.skipBlizzardVoiced end,
    function(v) WoWStoryVoiceDB.skipBlizzardVoiced = v end)
  optionChecks.indicator = makeCheck(frame, "Show NPC speaker indicator", -216,
    function() return WoWStoryVoiceDB.speakerIndicator ~= false end,
    function(v) WoWStoryVoiceDB.speakerIndicator = v end)

  local hint = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  hint:SetPoint("TOPLEFT", 24, -254)
  hint:SetWidth(400)
  hint:SetJustifyH("LEFT")
  hint:SetText("Companion volume, startup, update checks and addon installation are controlled in the Windows app.")

  makeButton(frame, "Test", 22, 18, 90, function()
    enqueueMessage("test", "", "Narrator", "WoW Story Voice is connected and ready.", true, false)
  end)
  makeButton(frame, "Stop speech", 120, 18, 105, function()
    clearTxQueue()
    enqueueMessage("control", "", "Narrator", "stop", true, true)
  end)
  makeButton(frame, "Status", 233, 18, 90, printStatus)
  makeButton(frame, "Close", 331, 18, 90, function() frame:Hide() end)

  frame:SetScript("OnShow", refreshOptionChecks)
  frame:Hide()
  optionsFrame = frame
  return frame
end

local function toggleOptions()
  local frame = createOptionsFrame()
  refreshOptionChecks()
  if frame:IsShown() then frame:Hide() else frame:Show() end
end

WSV:RegisterEvent("PLAYER_LOGIN")
WSV:RegisterEvent("PLAYER_TARGET_CHANGED")
WSV:RegisterEvent("PLAYER_FOCUS_CHANGED")
WSV:RegisterEvent("UPDATE_MOUSEOVER_UNIT")
WSV:RegisterEvent("PLAYER_INTERACTION_MANAGER_FRAME_SHOW")
WSV:RegisterEvent("PLAYER_INTERACTION_MANAGER_FRAME_HIDE")
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
  if event == "PLAYER_LOGIN" then
    heartbeatElapsed = HEARTBEAT_SEC
  elseif event == "PLAYER_INTERACTION_MANAGER_FRAME_SHOW" then
    currentInteractionType = select(1, ...)
    if UnitGUID("npc") then profiledGuidForUnit("npc") end
  elseif event == "PLAYER_INTERACTION_MANAGER_FRAME_HIDE" then
    local hiddenType = select(1, ...)
    if hiddenType == nil or hiddenType == currentInteractionType then currentInteractionType = nil end
  elseif event == "PLAYER_TARGET_CHANGED" then
    if UnitGUID("target") then profiledGuidForUnit("target") end
  elseif event == "PLAYER_FOCUS_CHANGED" then
    if UnitGUID("focus") then profiledGuidForUnit("focus") end
  elseif event == "UPDATE_MOUSEOVER_UNIT" then
    if UnitGUID("mouseover") then profiledGuidForUnit("mouseover") end
  elseif event == "QUEST_DETAIL" then
    if WoWStoryVoiceDB.questDialogue then capture("quest", GetQuestText()) end
  elseif event == "QUEST_PROGRESS" then
    if WoWStoryVoiceDB.questDialogue then capture("progress", GetProgressText()) end
  elseif event == "QUEST_COMPLETE" then
    if WoWStoryVoiceDB.questDialogue then capture("reward", GetRewardText()) end
  elseif event == "QUEST_GREETING" then
    if WoWStoryVoiceDB.questDialogue then capture("greeting", GetGreetingText()) end
  elseif event == "GOSSIP_SHOW" then
    if WoWStoryVoiceDB.gossipDialogue then
      local text = C_GossipInfo and C_GossipInfo.GetText and C_GossipInfo.GetText()
      capture("gossip", text)
    end
  elseif MONSTER_EVENT_KIND[event] then
    captureMonsterEvent(event, ...)
  end
end)

SLASH_WOWSTORYVOICE1 = "/wsv"
SlashCmdList.WOWSTORYVOICE = function(msg)
  local raw = msg or ""
  local command, arg = string.match(string.lower(raw), "^(%S*)%s*(%S*)")

  if command == "test" then
    enqueueMessage("test", "", "Narrator", "WoW Story Voice is connected and ready. Companion and addon version checking are active.", true, false)
    queueHeartbeat()
    print("|cff66ff66WoW Story Voice:|r test queued (v" .. VERSION .. ", WSV6 transport).")
  elseif command == "stop" then
    clearTxQueue()
    enqueueMessage("control", "", "Narrator", "stop", true, true)
    print("|cff66ff66WoW Story Voice:|r stop command sent.")
  elseif command == "config" or command == "options" then
    toggleOptions()
  elseif command == "quests" and (arg == "on" or arg == "off") then
    WoWStoryVoiceDB.questDialogue = (arg == "on")
    print("|cff66ff66WoW Story Voice:|r quest dialogue: " .. boolText(WoWStoryVoiceDB.questDialogue))
  elseif command == "gossip" and (arg == "on" or arg == "off") then
    WoWStoryVoiceDB.gossipDialogue = (arg == "on")
    print("|cff66ff66WoW Story Voice:|r gossip dialogue: " .. boolText(WoWStoryVoiceDB.gossipDialogue))
  elseif command == "blizzard" and (arg == "on" or arg == "off") then
    WoWStoryVoiceDB.skipBlizzardVoiced = (arg == "on")
    print("|cff66ff66WoW Story Voice:|r skip Blizzard subtitle/cinematic lines: " .. boolText(WoWStoryVoiceDB.skipBlizzardVoiced))
  elseif command == "monsters" and (arg == "on" or arg == "off") then
    WoWStoryVoiceDB.monsterDialogue = (arg == "on")
    print("|cff66ff66WoW Story Voice:|r ambient NPC dialogue: " .. boolText(WoWStoryVoiceDB.monsterDialogue))
  elseif command == "status" then
    printStatus()
  elseif command == "hide" then
    for _, p in ipairs(pixels) do p:Hide() end
  elseif command == "show" then
    enqueueMessage("test", "", "Narrator", "Bridge visible.", true, false)
  else
    print("|cff66ff66WoW Story Voice " .. VERSION .. "|r commands:")
    print("  /wsv config, /wsv test, /wsv stop, /wsv status, /wsv show, /wsv hide")
    print("  /wsv quests on|off, /wsv gossip on|off")
    print("  /wsv blizzard on|off, /wsv monsters on|off")
  end
end
