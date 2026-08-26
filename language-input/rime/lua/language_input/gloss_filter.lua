-- Candidate gloss filter for Language Input.
-- The pack format is UTF-8 TSV: candidate text<TAB>short gloss.

local M = {}

local function config_string(config, key, fallback)
  local value = config:get_string(key)
  if value == nil or value == "" then
    return fallback
  end
  return value
end

local function join_path(root, relative)
  local separator = package.config:sub(1, 1)
  relative = relative:gsub("[/\\]", separator)
  if root:sub(-1) == separator then
    return root .. relative
  end
  return root .. separator .. relative
end

local function load_pack(path)
  local glosses = {}
  local count = 0
  local handle, message = io.open(path, "rb")
  if handle == nil then
    return glosses, count, message
  end
  for line in handle:lines() do
    if line:sub(1, 1) ~= "#" then
      local word, gloss = line:match("^([^\t]+)\t(.+)$")
      if word ~= nil and gloss ~= nil then
        glosses[word] = gloss
        count = count + 1
      end
    end
  end
  handle:close()
  return glosses, count, nil
end

local function load_normalizer(filename)
  if Opencc == nil then
    return nil, "Opencc is unavailable"
  end
  local ok, normalizer = pcall(function()
    return Opencc(filename)
  end)
  if not ok or normalizer == nil then
    return nil, ok and "Opencc returned no converter" or tostring(normalizer)
  end
  return normalizer, nil
end

local function requires_ai(context)
  return context:get_option("language_input_ja")
    or context:get_option("language_input_es")
end

local function enforce_ai_provider(context)
  if requires_ai(context) and not context:get_option("language_input_ai") then
    context:set_option("language_input_ai", true)
  end
end

local function lookup_gloss(env, text)
  local gloss = env.glosses[text]
  if gloss ~= nil or env.normalizer == nil then
    return gloss
  end
  local ok, normalized = pcall(function()
    return env.normalizer:convert(text)
  end)
  if not ok or normalized == nil or normalized == "" or normalized == text then
    return nil
  end
  return env.glosses[normalized]
end

function M.init(env)
  local config = env.engine.schema.config
  env.language = config_string(config, "language_input/gloss_language", "en")
  local default_pack = "language_input/gloss/" .. env.language .. ".tsv"
  local relative_pack = config_string(
    config,
    "language_input/gloss_pack",
    default_pack
  )
  env.marker = "〔" .. env.language .. "·词〕 "
  env.pack_path = join_path(rime_api.get_shared_data_dir(), relative_pack)
  env.glosses, env.gloss_count, env.load_error = load_pack(env.pack_path)
  if env.load_error ~= nil then
    log.error(
      "Language Input: cannot open GlossPack "
        .. env.pack_path
        .. ": "
        .. env.load_error
    )
  else
    log.info(
      "Language Input: loaded "
        .. tostring(env.gloss_count)
        .. " "
        .. env.language
        .. " glosses"
    )
  end

  local normalizer_name = config_string(
    config,
    "language_input/gloss_normalizer",
    "t2s.json"
  )
  env.normalizer, env.normalizer_error = load_normalizer(normalizer_name)
  if env.normalizer == nil then
    log.warning(
      "Language Input: OpenCC normalization unavailable; exact lookup only: "
        .. env.normalizer_error
    )
  end

  local context = env.engine.context
  enforce_ai_provider(context)
  env.option_notifier = context.option_update_notifier:connect(
    function(updated_context, option_name)
      if option_name == "language_input_ai"
        or option_name == "language_input_ja"
        or option_name == "language_input_es" then
        enforce_ai_provider(updated_context)
      end
    end
  )
end

function M.fini(env)
  if env.option_notifier ~= nil then
    env.option_notifier:disconnect()
    env.option_notifier = nil
  end
end

function M.func(input, env)
  local context = env.engine.context
  enforce_ai_provider(context)
  local enabled = context:get_option("language_input_gloss")
  local sensitive = context:get_option("language_input_sensitive")
  local dictionary_mode = context:get_option("language_input_en")
    and not context:get_option("language_input_ai")
  for candidate in input:iter() do
    if enabled and not sensitive and dictionary_mode then
      local gloss = lookup_gloss(env, candidate.text)
      if gloss ~= nil then
        local comment = candidate.comment or ""
        if comment ~= "" then
          comment = comment .. "  "
        end
        comment = comment .. env.marker .. gloss
        yield(
          ShadowCandidate(
            candidate,
            "language_input_gloss",
            candidate.text,
            comment,
            false
          )
        )
      else
        yield(candidate)
      end
    else
      yield(candidate)
    end
  end
end

return M
