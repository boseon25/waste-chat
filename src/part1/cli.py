"""M1 CLI: contracts, synthetic cases, local validation, read-only DOCX probe."""
import argparse
import json
from pathlib import Path
import models
from examples import make_examples
from docx_probe import probe_docx
EXPORT_MODELS = [models.Fact[str], models.Evidence, models.InformationRequest, models.WasteCase,
                 models.Company, models.Site, models.CasePublished, models.ExtractionCandidate, models.Handoff]


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    for name in ('export-schema', 'make-examples'):
        sub.add_parser(name).add_argument('--out', required=True)
    v = sub.add_parser('validate')
    v.add_argument('file')
    v.add_argument('--model', choices=['WasteCase', 'Handoff', 'InformationRequest', 'CasePublished'], default='WasteCase')
    d = sub.add_parser('probe-docx')
    d.add_argument('file')
    d.add_argument('--out', required=True)
    args = p.parse_args()
    if args.action == 'export-schema':
        for model in EXPORT_MODELS:
            name = 'Fact' if model == models.Fact[str] else model.__name__
            schema = model.model_json_schema()
            schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
            schema['$comment'] = 'Structural contract only. Run Pydantic for local semantic/reference validation.'
            dump(Path(args.out) / (name + '.schema.json'), schema)
        print('Exported 9 schemas. Fact schema is Fact<string>; WasteCase embeds typed variants.')
    elif args.action == 'make-examples':
        for c in make_examples():
            dump(Path(args.out) / (c.case_id + '.json'), c.model_dump(mode='json'))
        print('Created 3 synthetic, unpublished cases (case_version=0).')
    elif args.action == 'validate':
        getattr(models, args.model).model_validate_json(Path(args.file).read_text(encoding='utf-8'))
        print(f'VALID {args.model}: {args.file} (not factual/legal verification)')
    else:
        result = probe_docx(args.file)
        dump(args.out, result)
        print(f"DOCX: {result['case_candidate_count']} candidates, 0 accepted, no DB writes")


if __name__ == '__main__':
    main()
