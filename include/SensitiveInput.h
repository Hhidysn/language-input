#pragma once

#include <iterator>
#include <windows.h>

namespace weasel {

enum class ConventionalInputState {
  kUnknown,
  kOrdinary,
  kSensitive,
};

inline ConventionalInputState QueryConventionalInputState(HWND window) {
  if (!window || !IsWindow(window))
    return ConventionalInputState::kUnknown;

  wchar_t class_name[64] = {};
  int class_name_length = GetClassNameW(
      window, class_name, static_cast<int>(std::size(class_name)));
  if (class_name_length <= 0)
    return ConventionalInputState::kUnknown;

  bool is_edit = _wcsicmp(class_name, L"Edit") == 0 ||
                 _wcsnicmp(class_name, L"RichEdit", 8) == 0;
  if (!is_edit)
    return ConventionalInputState::kUnknown;
  if ((GetWindowLongPtrW(window, GWL_STYLE) & ES_PASSWORD) != 0)
    return ConventionalInputState::kSensitive;

  DWORD_PTR password_character = 0;
  if (SendMessageTimeoutW(window, EM_GETPASSWORDCHAR, 0, 0,
                          SMTO_ABORTIFHUNG | SMTO_BLOCK, 50,
                          &password_character) == 0) {
    return ConventionalInputState::kUnknown;
  }
  return password_character == 0 ? ConventionalInputState::kOrdinary
                                 : ConventionalInputState::kSensitive;
}

inline bool IsConventionalPasswordControl(HWND window) {
  return QueryConventionalInputState(window) ==
         ConventionalInputState::kSensitive;
}

inline bool ShouldTreatInputAsSensitive(
    bool input_scope_known,
    bool input_scope_sensitive,
    ConventionalInputState conventional_state) {
  if (conventional_state == ConventionalInputState::kSensitive)
    return true;
  if (input_scope_known)
    return input_scope_sensitive;
  return conventional_state != ConventionalInputState::kOrdinary;
}

}  // namespace weasel
