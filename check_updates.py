#!/usr/bin/env python3
import argparse
import asyncio
import re
import sys
from itertools import groupby
from pathlib import Path
from typing import Optional, NamedTuple, AsyncIterable

import aiofiles
import aiohttp


PYPI_URL = "https://pypi.org/pypi/{package_name}/json"


class Package(NamedTuple):
    name: str
    required_version: str
    last_version: Optional[str] = None

    def __str__(self) -> str:
        return f"Package: '{self.name}'\n" \
               f"Required version: {self.required_version}\n" \
               f"Last version: {self.last_version}"


async def get_last_version(session: aiohttp.ClientSession,
                           package_name: str) -> Optional[str]:
    url = PYPI_URL.format(package_name=package_name)
    try:
        resp = await session.get(url)
    except Exception as e:
        print(f"{e.__class__.__name__}({e!r})", file=sys.stderr)
        return
    
    if resp.status == 200:
        json = await resp.json()
        return json['info']['version']


async def worker(args: asyncio.Queue,
                 results: asyncio.Queue,
                 session: aiohttp.ClientSession) -> None:
    while True:
        package = await args.get()

        last_version = await get_last_version(session, package.name)
        package = Package(
            name=package.name, 
            required_version=package.required_version,
            last_version=last_version
        )
        await results.put(package)

        args.task_done()


async def get_packages(project_path: Path) -> AsyncIterable[Package]:
    requirements_path = project_path / 'requirements.txt'
    if not requirements_path.exists():
        print("Requirements file not found", file=sys.stderr)
        return

    pattern = re.compile(r'([^<>= ]+)[<>= ]{2,4}(.+)')

    async with aiofiles.open(requirements_path) as r:
        async for requirement in r:
            name, version = pattern.search(requirement).groups()
            yield Package(name=name, required_version=version)


async def bound(project_path: Path) -> list[Package]:
    timeout = aiohttp.ClientTimeout(60)
    args = asyncio.Queue(maxsize=-1)
    results = asyncio.Queue(maxsize=-1)

    async with aiohttp.ClientSession(timeout=timeout) as ses:
        async for package in get_packages(project_path):
            await args.put(package)
        
        tasks = []
        for _ in range(5):
            task = asyncio.create_task(worker(args, results, ses))
            tasks += [task]

        await args.join()

        for task in tasks:
            task.cancel()

    return [
        results.get_nowait()
        for _ in range(results.qsize())
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check updates of the requirements"
    )
    parser.add_argument(
        '--path',
        type=Path,
        help="Path to the project",
        default=Path('.'),
        dest='path'
    )
    args = parser.parse_args()

    packages = asyncio.run(bound(args.path))

    key = lambda item: item.last_version != item.required_version    
    packages.sort(key=key)

    for has_update, packages_ in groupby(packages, key=key):
        if has_update:
            print("Packages with updates: ")
        else:
            print("Packages without updates: ")

        for num, package in enumerate(packages_, 1):
            print(f"{num}.\n{package}", end='\n-------------\n')
        print()


if __name__ == "__main__":
    main()
