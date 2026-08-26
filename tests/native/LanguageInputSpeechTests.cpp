#include <iostream>
#include <string>

#include "LanguageInputSpeech.h"
#include <SensitiveInput.h>

namespace {

int failures = 0;

void Check(bool condition, const char* message) {
  if (!condition) {
    ++failures;
    std::cerr << "FAILED: " << message << '\n';
  }
}

}  // namespace

int main() {
  using weasel::language_input::CandidateIndexForSpeech;
  using weasel::language_input::ParseGlossForSpeech;
  using weasel::ConventionalInputState;
  using weasel::QueryConventionalInputState;
  using weasel::ShouldTreatInputAsSensitive;

  auto first = CandidateIndexForSpeech(weasel::KeyEvent('1', 0));
  auto ninth = CandidateIndexForSpeech(weasel::KeyEvent('9', ibus::LOCK_MASK));
  Check(first && *first == 0, "1 should select candidate index 0");
  Check(ninth && *ninth == 8, "9 should select candidate index 8");
  Check(!CandidateIndexForSpeech(weasel::KeyEvent('0', 0)),
        "0 must not trigger candidate speech");
  Check(!CandidateIndexForSpeech(weasel::KeyEvent('1', ibus::CONTROL_MASK)),
        "modified number keys must not trigger candidate speech");
  Check(!CandidateIndexForSpeech(weasel::KeyEvent('1', ibus::RELEASE_MASK)),
        "key release must not trigger candidate speech");

  auto gloss = ParseGlossForSpeech("existing  〔en·词〕 hello; hi");
  Check(gloss && gloss->language == L"en" && gloss->text == L"hello; hi",
        "a dictionary gloss should parse after an existing comment");
  auto japanese = ParseGlossForSpeech("〔ja·AI〕 こんにちは");
  Check(japanese && japanese->language == L"ja" &&
            japanese->text == L"こんにちは",
        "an AI gloss should expose only its language prefix and text");
  auto spanish = ParseGlossForSpeech("〔es·AI〕 hola");
  Check(spanish && spanish->language == L"es" && spanish->text == L"hola",
        "a Spanish AI gloss should parse");
  Check(!ParseGlossForSpeech("ordinary candidate comment"),
        "ordinary comments must never be spoken");
  Check(!ParseGlossForSpeech("〔en〕 legacy marker"),
        "a source-less legacy marker must be rejected");
  Check(!ParseGlossForSpeech("〔e!·AI〕 invalid language"),
        "invalid language tags must be rejected");
  Check(!ParseGlossForSpeech("〔en·remote〕 invalid source"),
        "unknown source markers must be rejected");
  Check(!ParseGlossForSpeech("〔en·AI·词〕 ambiguous source"),
        "multiple source separators must be rejected");
  Check(!ParseGlossForSpeech("〔en·词〕 line\nbreak"),
        "multi-line speech text must be rejected");
  Check(!ParseGlossForSpeech("〔en·词〕"),
        "an empty marked gloss must be rejected");
  Check(!ParseGlossForSpeech(std::string("〔en·词〕 ") + std::string(513, 'a')),
        "oversized speech text must be rejected");
  Check(!ParseGlossForSpeech(std::string("〔en·词〕 ") + "\xff"),
        "invalid UTF-8 must be rejected");

  HWND parent =
      CreateWindowExW(0, L"STATIC", L"", WS_POPUP, 0, 0, 10, 10, nullptr,
                      nullptr, GetModuleHandleW(nullptr), nullptr);
  HWND ordinary_edit =
      CreateWindowExW(0, L"EDIT", L"", WS_CHILD, 0, 0, 10, 10, parent, nullptr,
                      GetModuleHandleW(nullptr), nullptr);
  HWND password_edit =
      CreateWindowExW(0, L"EDIT", L"", WS_CHILD | ES_PASSWORD, 0, 0, 10, 10,
                      parent, nullptr, GetModuleHandleW(nullptr), nullptr);
  HWND non_edit =
      CreateWindowExW(0, L"STATIC", L"", WS_CHILD, 0, 0, 10, 10, parent,
                      nullptr, GetModuleHandleW(nullptr), nullptr);
  Check(parent && ordinary_edit && password_edit && non_edit,
        "native controls should be creatable for the privacy test");
  Check(QueryConventionalInputState(ordinary_edit) ==
            ConventionalInputState::kOrdinary,
        "an ordinary Edit control must provide explicit non-sensitive evidence");
  Check(QueryConventionalInputState(password_edit) ==
            ConventionalInputState::kSensitive,
        "an ES_PASSWORD Edit control must provide sensitive evidence");
  Check(QueryConventionalInputState(non_edit) ==
            ConventionalInputState::kUnknown,
        "a non-edit control must not be treated as explicitly ordinary");
  Check(QueryConventionalInputState(nullptr) ==
            ConventionalInputState::kUnknown,
        "an invalid window must retain fail-closed unknown state");
  Check(!weasel::IsConventionalPasswordControl(ordinary_edit),
        "an ordinary Edit control must not be marked sensitive");
  Check(weasel::IsConventionalPasswordControl(password_edit),
        "an ES_PASSWORD Edit control must be marked sensitive");
  Check(!ShouldTreatInputAsSensitive(false, false,
                                     ConventionalInputState::kOrdinary),
        "ordinary Edit evidence must safely recover an unknown TSF scope");
  Check(ShouldTreatInputAsSensitive(false, false,
                                    ConventionalInputState::kUnknown),
        "unknown TSF and window evidence must remain fail-closed");
  Check(ShouldTreatInputAsSensitive(false, false,
                                    ConventionalInputState::kSensitive),
        "a conventional password control must remain sensitive");
  Check(!ShouldTreatInputAsSensitive(true, false,
                                     ConventionalInputState::kUnknown),
        "a readable ordinary TSF scope must not require window fallback");
  Check(ShouldTreatInputAsSensitive(true, true,
                                    ConventionalInputState::kOrdinary),
        "a sensitive TSF scope must override ordinary window evidence");
  Check(ShouldTreatInputAsSensitive(true, false,
                                    ConventionalInputState::kSensitive),
        "password window evidence must override an ordinary TSF scope");
  if (non_edit)
    DestroyWindow(non_edit);
  if (password_edit)
    DestroyWindow(password_edit);
  if (ordinary_edit)
    DestroyWindow(ordinary_edit);
  if (parent)
    DestroyWindow(parent);

  if (failures == 0)
    std::cout << "LanguageInputSpeechTests: all checks passed\n";
  return failures == 0 ? 0 : 1;
}
