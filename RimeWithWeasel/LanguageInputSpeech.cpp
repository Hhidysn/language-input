#include "stdafx.h"

#include "LanguageInputSpeech.h"

#include <sapi.h>
#include <sphelper.h>

#include <algorithm>
#include <cwchar>
#include <limits>
#include <utility>

#pragma comment(lib, "sapi.lib")

namespace weasel::language_input {
namespace {

std::optional<std::wstring> Utf8ToWide(std::string_view text) {
  if (text.empty())
    return std::wstring();
  if (text.size() > static_cast<size_t>((std::numeric_limits<int>::max)()))
    return std::nullopt;
  int required = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(),
                                     static_cast<int>(text.size()), nullptr, 0);
  if (required <= 0)
    return std::nullopt;
  std::wstring result(static_cast<size_t>(required), L'\0');
  int converted = MultiByteToWideChar(
      CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()),
      result.data(), static_cast<int>(result.size()));
  if (converted != required)
    return std::nullopt;
  return result;
}

bool IsLanguageCharacter(char value) {
  return (value >= 'a' && value <= 'z') || (value >= 'A' && value <= 'Z') ||
         (value >= '0' && value <= '9') || value == '-';
}

std::wstring LocaleForLanguage(std::wstring language) {
  std::replace(language.begin(), language.end(), L'_', L'-');
  if (_wcsicmp(language.c_str(), L"en") == 0)
    return L"en-US";
  if (_wcsicmp(language.c_str(), L"zh") == 0)
    return L"zh-CN";
  return language;
}

}  // namespace

std::optional<size_t> CandidateIndexForSpeech(const KeyEvent& key_event) {
  constexpr UINT kDisallowedModifiers = ibus::SHIFT_MASK | ibus::CONTROL_MASK |
                                        ibus::ALT_MASK | ibus::SUPER_MASK |
                                        ibus::HYPER_MASK | ibus::META_MASK |
                                        ibus::RELEASE_MASK;
  if ((key_event.mask & kDisallowedModifiers) != 0)
    return std::nullopt;
  if (key_event.keycode < '1' || key_event.keycode > '9')
    return std::nullopt;
  return static_cast<size_t>(key_event.keycode - '1');
}

std::optional<GlossSpeech> ParseGlossForSpeech(std::string_view comment) {
  constexpr std::string_view kMarkerStart = u8"〔";
  constexpr std::string_view kMarkerEnd = u8"〕";
  size_t marker = comment.rfind(kMarkerStart);
  if (marker == std::string_view::npos)
    return std::nullopt;
  size_t language_start = marker + kMarkerStart.size();
  size_t language_end = comment.find(kMarkerEnd, language_start);
  if (language_end == std::string_view::npos)
    return std::nullopt;

  std::string_view language =
      comment.substr(language_start, language_end - language_start);
  if (language.size() < 2 || language.size() > 35 ||
      !std::all_of(language.begin(), language.end(), IsLanguageCharacter)) {
    return std::nullopt;
  }

  size_t gloss_start = language_end + kMarkerEnd.size();
  if (gloss_start >= comment.size() || comment[gloss_start] != ' ')
    return std::nullopt;
  while (gloss_start < comment.size() && comment[gloss_start] == ' ')
    ++gloss_start;
  std::string_view gloss = comment.substr(gloss_start);
  if (gloss.empty() || gloss.size() > 512 ||
      gloss.find_first_of("\r\n\t") != std::string_view::npos) {
    return std::nullopt;
  }

  auto wide_language = Utf8ToWide(language);
  auto wide_gloss = Utf8ToWide(gloss);
  if (!wide_language || !wide_gloss || wide_gloss->empty())
    return std::nullopt;
  return GlossSpeech{std::move(*wide_language), std::move(*wide_gloss)};
}

SpeechService::SpeechService() {
  CoCreateInstance(CLSID_SpVoice, nullptr, CLSCTX_INPROC_SERVER, IID_ISpVoice,
                   reinterpret_cast<void**>(&voice_));
}

SpeechService::~SpeechService() {
  Stop();
  if (voice_)
    voice_->Release();
}

void SpeechService::SelectVoiceForLanguage(const std::wstring& language) {
  if (!voice_ || _wcsicmp(selected_language_.c_str(), language.c_str()) == 0)
    return;
  selected_language_ = language;

  std::wstring locale_name = LocaleForLanguage(language);
  LCID locale =
      LocaleNameToLCID(locale_name.c_str(), LOCALE_ALLOW_NEUTRAL_NAMES);
  if (locale == 0)
    return;

  wchar_t filter[32] = {};
  swprintf_s(filter, L"Language=%x", LANGIDFROMLCID(locale));
  IEnumSpObjectTokens* voices = nullptr;
  if (FAILED(SpEnumTokens(SPCAT_VOICES, filter, nullptr, &voices)) || !voices)
    return;

  ISpObjectToken* token = nullptr;
  ULONG fetched = 0;
  if (voices->Next(1, &token, &fetched) == S_OK && token) {
    voice_->SetVoice(token);
    token->Release();
  }
  voices->Release();
}

bool SpeechService::Speak(const GlossSpeech& gloss) {
  if (!voice_ || gloss.text.empty())
    return false;
  SelectVoiceForLanguage(gloss.language);
  constexpr DWORD kFlags = SPF_ASYNC | SPF_PURGEBEFORESPEAK | SPF_IS_NOT_XML;
  return SUCCEEDED(voice_->Speak(gloss.text.c_str(), kFlags, nullptr));
}

void SpeechService::Stop() {
  if (voice_)
    voice_->Speak(L"", SPF_ASYNC | SPF_PURGEBEFORESPEAK, nullptr);
}

}  // namespace weasel::language_input
