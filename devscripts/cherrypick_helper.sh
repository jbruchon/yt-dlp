#!/bin/bash

EXIT=0

echo -e "Helper to git cherry-pick from other repositories\n"

while true
	do echo -n "Paste repo link (will trim for you, 'x' to keep existing): "
	read X
	if [ "$X" != "x" ]
		then X="${X/yt-dlp*/yt-dlp}"
		echo -e "\nUsing repo: $X"
		git remote remove temp 2>/dev/null
		git remote add temp "$X" || exit 1
		git fetch --depth=30 temp || exit 1
	fi
	while true
		do
		echo -e "\nPaste a cherry-pick hash, s for shell, n for new pick source, x to stop\n"
		read -r X
		[ -z "$X" ] && continue
		[ "$X" = "x" ] && EXIT=1 && break
		[ "$X" = "n" ] && echo -e "\nStarting new pick session\n" && break
		if [ "$X" = "s" ]; then bash; echo -e "\nResuming cherry-pick helper\n"; continue; fi
		git cherry-pick $X
	done
	[ $EXIT -eq 1 ] && break
done
echo "Exiting."
