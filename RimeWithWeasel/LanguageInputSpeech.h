#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>

#include <windows.h>
#include <KeyEvent.h>

struct ISpVoice;

namespace weasel::language_input {

struct GlossSpeech {
  std::wstring language;
  std::wstring text;
};

// Returns the zero-based index selected by an unmodified 1..9 key press.
std::optional<size_t> CandidateIndexForSpeech(const KeyEvent& key_event);

// Extracts only comments carrying our explicit marker: 〔language〕 gloss.
std::optional<GlossSpeech> ParseGlossForSpeech(std::string_view comment);

class SpeechService {
 public:
  SpeechService();
  ~SpeechService();

  SpeechService(const SpeechService&) = delete;
  SpeechService& operator=(const SpeechService&) = delete;

  bool Speak(const GlossSpeech& gloss);
  void Stop();

 private:
  void SelectVoiceForLanguage(const std::wstring& language);

  ISpVoice* voice_ = nullptr;
  std::wstring selected_language_;
};

}  // namespace weasel::language_input
