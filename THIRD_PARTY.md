# Сторонние компоненты

MIT в этом репозитории относится к его собственным файлам. Код зависимостей, исполняемые файлы и веса моделей здесь не распространяются.

| Компонент | Назначение | Источник условий |
| --- | --- | --- |
| Allosaurus 1.0.2 | Внешний процесс фонетического распознавания | [Репозиторий](https://github.com/xinjli/allosaurus), [GPL-3.0](https://github.com/xinjli/allosaurus/blob/master/LICENSE) |
| Предобученная uni2005 | Веса универсальной модели, загружаемые Allosaurus | [Официальные релизы](https://github.com/xinjli/allosaurus/releases), условия соответствующего артефакта |
| ZIPA Small CRCTC NS | Внешняя ONNX-модель распознавания звуков | [Модель и её файлы](https://huggingface.co/anyspeech/zipa-small-crctc-ns-700k), [код ZIPA и MIT](https://github.com/lingjzhu/zipa/blob/main/LICENSE); MIT кода не заменяет условия весов |
| Wav2Vec2Phoneme | Внешняя модель Meta с фонетическим выходом | [Карточка модели, Apache-2.0](https://huggingface.co/facebook/wav2vec2-xlsr-53-espeak-cv-ft) |
| Silero VAD | Общая локальная модель речевой активности для разметки пауз | [Репозиторий и закреплённая ревизия](https://github.com/snakers4/silero-vad/tree/60b7ffa243625ebdc1070275a29f18c87843786a), [MIT](https://github.com/snakers4/silero-vad/blob/60b7ffa243625ebdc1070275a29f18c87843786a/LICENSE) |
| ONNX Runtime, Lhotse, Transformers, truststore | Исполнение и загрузка новых движков | [ONNX Runtime](https://github.com/microsoft/onnxruntime), [Lhotse](https://github.com/lhotse-speech/lhotse), [Transformers](https://github.com/huggingface/transformers), [truststore](https://github.com/sethmlarson/truststore) |
| FFmpeg | Преобразование аудио | [Лицензирование FFmpeg](https://ffmpeg.org/legal.html); зависит от состава сборки |
| Python, PyTorch и зависимости Allosaurus | Исполнение распознавателя | Условия соответствующих проектов и установленных пакетов |

При распространении окружения, готовой сборки или моделей отдельно проверьте условия включённых компонентов. Проект не выдаёт MIT-разрешение на чужие веса или код.

Ссылки на лингвистические работы приведены в README-profiles.md. Полные тексты этих работ в репозиторий не включены.
