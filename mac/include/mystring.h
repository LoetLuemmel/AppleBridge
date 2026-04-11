/*
 * AppleBridge - Simple String Functions Header
 */

#ifndef MYSTRING_H
#define MYSTRING_H

#ifndef __SIZE_T__
#define __SIZE_T__
typedef unsigned int size_t;
#endif

size_t mystrlen(const char *s);
char *mystrcpy(char *dest, const char *src);
char *mystrncpy(char *dest, const char *src, size_t n);
char *mystrcat(char *dest, const char *src);
int mystrcmp(const char *s1, const char *s2);
int mystrncmp(const char *s1, const char *s2, size_t n);

/* Macros to replace standard functions */
#define strlen mystrlen
#define strcpy mystrcpy
#define strncpy mystrncpy
#define strcat mystrcat
#define strcmp mystrcmp
#define strncmp mystrncmp

#endif
