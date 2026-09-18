local Indicator = CreateFrame("Frame")

-- Visual-only layer. It listens to the same Blizzard dialogue events as Core.lua
-- and never changes the WSV6 bridge or speech payload.
local activeGuid = ""
local activeName = ""
local activeUntil = 0
local activeUnit = nil
local nameplateUnits = {}
local pulseElapsed = 0

WoWStoryVoiceDB = WoWStoryVoiceDB or {}
if WoWStoryVoiceDB.speakerIndicator == nil then WoWStoryVoiceDB.speakerIndicator = true end

local function enabled()
  return WoWStoryVoiceDB.speakerIndicator ~= false
end

local function now()
  return GetTime and GetTime() or 0
end

local function estimateDuration(text)
  if type(text) ~= "string" then return 3.0 end
  -- Deliberately generous because TTS generation and the companion speech queue
  -- add some latency. This is an indicator, not a lip-sync clock.
  local seconds = 1.8 + (#text / 13.5)
  if seconds < 3.0 then seconds = 3.0 end
  if seconds > 30.0 then seconds = 30.0 end
  return seconds
end

local function createPlateIndicator()
  local frame = CreateFrame("Frame", nil, UIParent)
  frame:SetSize(72, 24)
  frame:SetFrameStrata("HIGH")
  frame:Hide()

  local bg = frame:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints()
  bg:SetColorTexture(0.02, 0.02, 0.025, 0.84)

  local accent = frame:CreateTexture(nil, "BORDER")
  accent:SetPoint("BOTTOMLEFT", 0, 0)
  accent:SetPoint("BOTTOMRIGHT", 0, 0)
  accent:SetHeight(2)
  accent:SetColorTexture(1.0, 0.78, 0.16, 0.95)

  frame.bars = {}
  local heights = { 7, 14, 10 }
  for i = 1, 3 do
    local bar = frame:CreateTexture(nil, "ARTWORK")
    bar:SetWidth(3)
    bar:SetHeight(heights[i])
    bar:SetPoint("BOTTOMLEFT", 8 + ((i - 1) * 5), 5)
    bar:SetColorTexture(1.0, 0.82, 0.24, 1)
    frame.bars[i] = bar
  end

  local label = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  label:SetPoint("LEFT", 27, 0)
  label:SetText("VOICE")
  label:SetTextColor(1.0, 0.84, 0.30)
  frame.label = label

  return frame
end

local function createFallbackBanner()
  local frame = CreateFrame("Frame", nil, UIParent)
  frame:SetSize(360, 38)
  frame:SetPoint("TOP", UIParent, "TOP", 0, -105)
  frame:SetFrameStrata("HIGH")
  frame:Hide()

  local bg = frame:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints()
  bg:SetColorTexture(0.02, 0.02, 0.025, 0.82)

  local accent = frame:CreateTexture(nil, "BORDER")
  accent:SetPoint("BOTTOMLEFT", 0, 0)
  accent:SetPoint("BOTTOMRIGHT", 0, 0)
  accent:SetHeight(2)
  accent:SetColorTexture(1.0, 0.78, 0.16, 0.95)

  local text = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
  text:SetPoint("CENTER")
  text:SetTextColor(1.0, 0.88, 0.42)
  frame.text = text
  return frame
end

local plateIndicator = createPlateIndicator()
local fallbackBanner = createFallbackBanner()

local function hideIndicator()
  plateIndicator:Hide()
  fallbackBanner:Hide()
  activeUnit = nil
end

local function findUnitForGuid(guid)
  if type(guid) ~= "string" or guid == "" then return nil end

  local directUnits = { "npc", "target", "focus", "mouseover" }
  for _, unit in ipairs(directUnits) do
    if UnitGUID(unit) == guid then return unit end
  end

  return nameplateUnits[guid]
end

local function safePlateForUnit(unit)
  if not unit or not C_NamePlate or not C_NamePlate.GetNamePlateForUnit then return nil end
  local ok, plate = pcall(C_NamePlate.GetNamePlateForUnit, unit)
  if not ok or not plate then return nil end
  if plate.IsForbidden and plate:IsForbidden() then return nil end
  return plate
end

local function showFallback()
  plateIndicator:Hide()
  activeUnit = nil
  if activeName ~= "" and activeName ~= "Unknown" and activeName ~= "Narrator" then
    fallbackBanner.text:SetText("Speaking: " .. activeName)
    fallbackBanner:Show()
  else
    fallbackBanner:Hide()
  end
end

local function attachToSpeaker()
  if not enabled() or activeUntil <= now() then
    hideIndicator()
    return false
  end

  local unit = findUnitForGuid(activeGuid)
  local plate = safePlateForUnit(unit)
  if not plate then
    showFallback()
    return false
  end

  local ok = pcall(function()
    plateIndicator:SetParent(plate)
    plateIndicator:ClearAllPoints()
    plateIndicator:SetPoint("BOTTOM", plate, "TOP", 0, 12)
    plateIndicator:SetFrameLevel((plate:GetFrameLevel() or 1) + 20)
  end)
  if not ok then
    plateIndicator:SetParent(UIParent)
    showFallback()
    return false
  end

  activeUnit = unit
  fallbackBanner:Hide()
  plateIndicator:Show()
  return true
end

local function activateSpeaker(guid, name, text)
  if not enabled() then return end
  if type(name) ~= "string" or name == "" then name = "Unknown" end
  if type(guid) ~= "string" then guid = "" end

  activeGuid = guid
  activeName = name
  activeUntil = now() + estimateDuration(text)
  attachToSpeaker()
end

local function captureCurrentNpc(text)
  local unit = nil
  if UnitGUID("npc") then
    unit = "npc"
  elseif UnitGUID("target") then
    unit = "target"
  end
  if not unit then return end
  activateSpeaker(UnitGUID(unit) or "", UnitName(unit) or "Unknown", text)
end

Indicator:RegisterEvent("NAME_PLATE_UNIT_ADDED")
Indicator:RegisterEvent("NAME_PLATE_UNIT_REMOVED")
Indicator:RegisterEvent("QUEST_DETAIL")
Indicator:RegisterEvent("QUEST_PROGRESS")
Indicator:RegisterEvent("QUEST_COMPLETE")
Indicator:RegisterEvent("QUEST_GREETING")
Indicator:RegisterEvent("GOSSIP_SHOW")
Indicator:RegisterEvent("CHAT_MSG_MONSTER_SAY")
Indicator:RegisterEvent("CHAT_MSG_MONSTER_YELL")
Indicator:RegisterEvent("CHAT_MSG_MONSTER_WHISPER")
Indicator:RegisterEvent("CHAT_MSG_MONSTER_PARTY")

Indicator:SetScript("OnEvent", function(_, event, ...)
  if event == "NAME_PLATE_UNIT_ADDED" then
    local unit = select(1, ...)
    local guid = UnitGUID(unit)
    if guid then
      nameplateUnits[guid] = unit
      if guid == activeGuid and activeUntil > now() then attachToSpeaker() end
    end
    return
  elseif event == "NAME_PLATE_UNIT_REMOVED" then
    local unit = select(1, ...)
    local guid = UnitGUID(unit)
    if guid and nameplateUnits[guid] == unit then nameplateUnits[guid] = nil end
    if unit == activeUnit then showFallback() end
    return
  end

  if not enabled() then return end

  if event == "QUEST_DETAIL" then
    if WoWStoryVoiceDB.questDialogue ~= false then captureCurrentNpc(GetQuestText()) end
  elseif event == "QUEST_PROGRESS" then
    if WoWStoryVoiceDB.questDialogue ~= false then captureCurrentNpc(GetProgressText()) end
  elseif event == "QUEST_COMPLETE" then
    if WoWStoryVoiceDB.questDialogue ~= false then captureCurrentNpc(GetRewardText()) end
  elseif event == "QUEST_GREETING" then
    if WoWStoryVoiceDB.questDialogue ~= false then captureCurrentNpc(GetGreetingText()) end
  elseif event == "GOSSIP_SHOW" then
    if WoWStoryVoiceDB.gossipDialogue ~= false then
      local text = C_GossipInfo and C_GossipInfo.GetText and C_GossipInfo.GetText()
      captureCurrentNpc(text)
    end
  else
    if WoWStoryVoiceDB.monsterDialogue == false then return end
    local text = select(1, ...)
    local name = select(2, ...)
    local guid = select(12, ...)
    local isSubtitle = select(15, ...)
    local hideSenderInLetterbox = select(16, ...)
    if WoWStoryVoiceDB.skipBlizzardVoiced and (isSubtitle == true or hideSenderInLetterbox == true) then return end
    if type(text) == "string" and text ~= "" then
      activateSpeaker(guid or "", name or "Unknown", text)
    end
  end
end)

Indicator:SetScript("OnUpdate", function(_, elapsed)
  if activeUntil <= 0 then return end
  if not enabled() or now() >= activeUntil then
    activeGuid = ""
    activeName = ""
    activeUntil = 0
    hideIndicator()
    return
  end

  pulseElapsed = pulseElapsed + elapsed
  if pulseElapsed >= 0.08 then
    pulseElapsed = 0
    local t = now() * 6
    local pulse = 0.72 + (0.28 * ((math.sin(t) + 1) / 2))
    plateIndicator:SetAlpha(pulse)
  end

  if activeUnit and UnitGUID(activeUnit) ~= activeGuid then
    attachToSpeaker()
  elseif not activeUnit then
    attachToSpeaker()
  end
end)

SLASH_WSVSPEAKERINDICATOR1 = "/wsvindicator"
SlashCmdList.WSVSPEAKERINDICATOR = function(msg)
  local command = string.lower((msg or ""):match("^%s*(.-)%s*$") or "")
  if command == "on" then
    WoWStoryVoiceDB.speakerIndicator = true
    print("|cff66ff66WoW Story Voice:|r NPC speaker indicator: ON")
  elseif command == "off" then
    WoWStoryVoiceDB.speakerIndicator = false
    hideIndicator()
    print("|cff66ff66WoW Story Voice:|r NPC speaker indicator: OFF")
  else
    print("|cff66ff66WoW Story Voice:|r /wsvindicator on|off")
  end
end
