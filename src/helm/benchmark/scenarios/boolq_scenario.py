import json
import os
from typing import List, Dict

from helm.common.general import ensure_file_downloaded, ensure_directory_exists
from .scenario import Scenario, Instance, Reference, TRAIN_SPLIT, VALID_SPLIT, CORRECT_TAG, PassageQuestionInput


class BoolQScenario(Scenario):
    """
    The BoolQ dataset is from the paper:
    https://arxiv.org/abs/1905.10044

    Original repository can be found at:
    https://github.com/google-research-datasets/boolean-questions

    BoolQ is a QA dataset containing 15942 (9427 training, 3270 dev, 3245 test) boolean (Yes/No) questions.
    Each sample contains a passage, a question and an answer that are generated in an unconstrained/unprompted setting.

    We prompt models using the following format

        <passage>
        Question: <question>?
        Answer:

        Target completion:
            <answer>

    Using an example from the training dataset, we have

        Context: Epsom railway station serves the town of Epsom in Surrey. It is located off Waterloo Road and is
        less than two minutes' walk from the High Street.
        It is not in the London Oyster card zone unlike Epsom Downs or Tattenham Corner stations.
        The station building was replaced in 2012/2013 with a new building with apartments above the station.
        Question: Can you use oyster card at epsom station?
        Answer:

        Target completion:
            Yes

    We also integrate contrast sets for this dataset from the paper:
    https://arxiv.org/abs/2004.02709

    Original repository can be found at:
    https://github.com/allenai/contrast-sets

    Each sample contains the original <passage, question, answer> triplet, and the human-perturbed version
    i.e. <passage, perturbed question, perturbed answer>.

    Contrast Sets for BoolQ contains 339 perturbed questions, forming 70 contrast sets in total.
    Perturbations to the original questions are generated by humans, with the intention of flipping the gold label.
    For more details, see the original paper, Appendix B.9.

    An example instance of a perturbation (from the original paper):

    ```
    The Fate of the Furious premiered in Berlin on April 4, 2017, and was theatrically released in the
    United States on April 14, 2017, playing in 3D, IMAX 3D and 4DX internationally. . . A spinoff film starring
    Johnson and Statham’s characters is scheduled for release in August 2019, while the ninth and tenth films are
    scheduled for releases on the years 2020 and 2021.
    question: is “Fate and the Furious” the last movie?
    answer: no

    perturbed question: is “Fate and the Furious” the first of multiple movies?
    perturbed answer: Yes
    perturbation strategy: adjective change.
    ```
    """

    name = "boolq"
    description = "Question answering dataset with naturally occuring Yes/No questions."
    tags = ["question_answering"]

    def __init__(self, only_contrast=False):
        """
        Args:
          only_contrast: Produce only inputs that have a contrast version.
        """
        self.only_contrast = only_contrast

    def get_context(self, passage: str, question: str) -> str:
        """
        We follow the format from https://arxiv.org/abs/2005.14165.
        For more details, see Figure G.29: Formatted dataset example for BoolQ.
        """
        question = question.strip().capitalize()
        assert question[-1] != "?"
        question += "?"
        return PassageQuestionInput(passage=passage, question=question).to_text()

    def get_split_instances(self, split: str, path: str, contrast_map: dict) -> List[Instance]:
        split_instances: List[Instance] = []
        with open(path, "r") as f:
            all_triplets = list(f)
            for item in all_triplets:
                triplet: Dict = json.loads(item)
                passage: str = triplet["passage"]
                question: str = triplet["question"]
                answer: bool = triplet["answer"]

                correct_answer: str = "Yes" if answer else "No"
                context: str = self.get_context(passage, question)

                contrast_inputs, contrast_references = None, None
                if question in contrast_map:
                    assert correct_answer == contrast_map[question]["original_answer"]
                    contrast_inputs = [
                        self.get_context(passage, q) for q in contrast_map[question]["perturbed_questions"]
                    ]
                    contrast_references = [
                        [Reference(output=perturbed_answer, tags=[CORRECT_TAG])]
                        for perturbed_answer in contrast_map[question]["perturbed_answers"]
                    ]
                elif self.only_contrast and split == VALID_SPLIT:
                    continue

                instance: Instance = Instance(
                    input=context,
                    references=[Reference(output=correct_answer, tags=[CORRECT_TAG])],
                    split=split,
                    contrast_inputs=contrast_inputs,
                    contrast_references=contrast_references,
                )
                split_instances.append(instance)
        return split_instances

    def get_instances(self) -> List[Instance]:
        data_path: str = os.path.join(self.output_path, "data")
        ensure_directory_exists(data_path)

        instances: List[Instance] = []
        split_to_filename: Dict[str, str] = {TRAIN_SPLIT: "train", VALID_SPLIT: "dev"}

        # First, ensure all splits are downloaded
        for split, filename in split_to_filename.items():
            url: str = f"https://storage.googleapis.com/boolq/{filename}.jsonl"
            target_path: str = os.path.join(data_path, filename)
            ensure_file_downloaded(source_url=url, target_path=target_path, unpack=False)

        # Ensure contrast set is downloaded and construct the contrast map
        contrast_url: str = "https://raw.githubusercontent.com/allenai/contrast-sets/main/BoolQ/boolq_perturbed.json"
        contrast_path: str = os.path.join(data_path, "boolq_perturbed.jsonl")
        ensure_file_downloaded(source_url=contrast_url, target_path=contrast_path, unpack=False)

        with open(contrast_path, encoding="utf-8") as f:
            all_questions = json.load(f)

        contrast_map: dict = {}

        for item in all_questions["data"][1:]:
            original_question: str = item["question"]
            original_answer: str = "Yes" if item["answer"] == "TRUE" else "No"

            assert original_question not in contrast_map

            contrast_map[original_question] = {
                "original_answer": original_answer,
                "perturbed_questions": [],
                "perturbed_answers": [],
            }

            for perturbed_item in item["perturbed_questions"]:
                perturbed_question: str = perturbed_item["perturbed_q"]
                perturbed_answer: str = "Yes" if perturbed_item["answer"] == "TRUE" else "No"
                # only keep contrast questions that actually change the answer
                if not perturbed_question or perturbed_answer == original_answer:
                    continue
                contrast_map[original_question]["perturbed_questions"].append(perturbed_question)
                contrast_map[original_question]["perturbed_answers"].append(perturbed_answer)

            if len(contrast_map[original_question]["perturbed_questions"]) == 0:
                del contrast_map[original_question]

        for split, filename in split_to_filename.items():
            split_path: str = os.path.join(data_path, filename)
            instances.extend(self.get_split_instances(split, split_path, contrast_map if split == VALID_SPLIT else {}))
        return instances
